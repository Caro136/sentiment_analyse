"""语义聚类与实体抽取测试。

聚类用**假 embedder** 驱动：它按文本中的关键词返回预设向量，
于是"语义相近的文本向量相近"这件事变得可控可断言，不依赖真实编码服务。
"""

import asyncio
import unittest

from engines.analytics.clustering import (
    ClusterInfo,
    ClusteringResult,
    _determine_k,
    _normalize,
    _single_cluster,
    cluster_texts,
    label_clusters,
)
from engines.analytics.entities import (
    RELATION_COOCCURRENCE,
    EntityRelationEdge,
    ExtractionResult,
    extract_entities,
    make_entity_id,
    make_relation_id,
)
from engines.analytics.repository import make_event_id, scoped_cluster_id
from engines.analytics.schemas import EntityExtraction, ExtractedEntity
from engines.contracts.content import Author, UnifiedContent
from engines.contracts.enums import SourceType


class FakeEmbedder:
    """按关键词分组返回向量：同一组的文本得到相同向量。

    这让"语义相近 → 向量相近"可控，从而能断言聚类的分组行为。
    """

    def __init__(self, groups: dict[str, int], dim: int = 8) -> None:
        self._groups = groups
        self._dim = dim
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        vectors = []
        for text in texts:
            group = next((g for keyword, g in self._groups.items()
                          if keyword in text), -1)
            vec = [0.0] * self._dim
            vec[group % self._dim if group >= 0 else self._dim - 1] = 1.0
            vectors.append(vec)
        return vectors


class BrokenEmbedder:
    async def embed(self, texts):
        raise RuntimeError("编码服务不可用")


class NormalizeTest(unittest.TestCase):
    def test_rows_become_unit_vectors(self) -> None:
        import numpy as np

        matrix = _normalize(np.asarray([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32))
        self.assertAlmostEqual(float(np.linalg.norm(matrix[0])), 1.0, places=5)
        # 零向量不该产生 NaN/除零
        self.assertEqual(float(np.linalg.norm(matrix[1])), 0.0)


class DetermineKTest(unittest.TestCase):
    def test_small_sample_yields_single_cluster(self) -> None:
        self.assertEqual(_determine_k(4, min_size=3, max_k=12), 1)

    def test_scales_with_count(self) -> None:
        self.assertEqual(_determine_k(30, min_size=3, max_k=12), 10)
        self.assertEqual(_determine_k(60, min_size=3, max_k=12), 12)   # 受上限约束

    def test_at_least_two_for_medium_sample(self) -> None:
        self.assertGreaterEqual(_determine_k(10, min_size=3, max_k=12), 2)


class ClusteringTest(unittest.TestCase):
    def test_empty_input(self) -> None:
        result = asyncio.run(cluster_texts([]))
        self.assertFalse(result.ok)
        self.assertTrue(result.degraded_reason)

    def test_too_few_items_yields_single_cluster(self) -> None:
        result = asyncio.run(cluster_texts([("a", "内容一"), ("b", "内容二")]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result.method, "single")

    def test_groups_semantically_similar_texts(self) -> None:
        """含同一关键词的文本应聚到一起。"""
        embedder = FakeEmbedder({"热": 0, "贵": 1})
        items = [
            ("a1", "手机太热了"), ("a2", "发热严重"), ("a3", "机身很热"),
            ("b1", "价格太贵"), ("b2", "贵得离谱"), ("b3", "售价偏高贵的很"),
        ]
        result = asyncio.run(cluster_texts(items, embedder=embedder))

        self.assertEqual(len(result), 2)
        # 同组文本应落在同一簇
        self.assertEqual(result.assignments["a1"], result.assignments["a2"])
        self.assertEqual(result.assignments["b1"], result.assignments["b2"])
        self.assertNotEqual(result.assignments["a1"], result.assignments["b1"])

    def test_cluster_members_cover_all_inputs(self) -> None:
        embedder = FakeEmbedder({"热": 0, "贵": 1})
        items = [(f"u{i}", "热" if i % 2 else "贵") for i in range(8)]
        result = asyncio.run(cluster_texts(items, embedder=embedder))
        members = [uid for cluster in result.clusters for uid in cluster.member_ids]
        self.assertEqual(sorted(members), sorted(uid for uid, _ in items))
        self.assertEqual(len(result.assignments), len(items))

    def test_clusters_sorted_by_size_desc(self) -> None:
        embedder = FakeEmbedder({"热": 0, "贵": 1})
        items = [("h1", "热"), ("h2", "热"), ("h3", "热"), ("h4", "热"),
                 ("g1", "贵"), ("g2", "贵"), ("g3", "贵")]
        result = asyncio.run(cluster_texts(items, embedder=embedder))
        sizes = [c.size for c in result.clusters]
        self.assertEqual(sizes, sorted(sizes, reverse=True))

    def test_representative_ids_are_members(self) -> None:
        embedder = FakeEmbedder({"热": 0, "贵": 1})
        items = [(f"u{i}", "热" if i % 2 else "贵") for i in range(10)]
        result = asyncio.run(cluster_texts(items, embedder=embedder))
        for cluster in result.clusters:
            for uid in cluster.representative_ids:
                self.assertIn(uid, cluster.member_ids)

    def test_dominant_sentiment_aggregated(self) -> None:
        embedder = FakeEmbedder({"热": 0, "贵": 1})
        items = [("h1", "热"), ("h2", "热"), ("h3", "热"), ("g1", "贵"), ("g2", "贵")]
        sentiments = {"h1": "negative", "h2": "negative", "h3": "neutral",
                      "g1": "positive", "g2": "neutral"}
        result = asyncio.run(cluster_texts(items, embedder=embedder, sentiments=sentiments))
        hot_cluster = next(c for c in result.clusters if "h1" in c.member_ids)
        self.assertEqual(hot_cluster.dominant_sentiment, "negative")

    def test_embedding_failure_degrades_to_single_cluster(self) -> None:
        """**核心约束**：编码服务不可用时不该让整条链路中断。"""
        items = [(f"u{i}", f"内容{i}") for i in range(10)]
        result = asyncio.run(cluster_texts(items, embedder=BrokenEmbedder()))
        self.assertEqual(len(result), 1)
        self.assertIn("编码失败", result.degraded_reason)
        self.assertEqual(len(result.assignments), len(items))

    def test_blank_texts_filtered_out(self) -> None:
        embedder = FakeEmbedder({"热": 0})
        items = [("a", "热"), ("b", "   "), ("c", ""), ("d", "热")]
        result = asyncio.run(cluster_texts(items, embedder=embedder))
        members = [uid for c in result.clusters for uid in c.member_ids]
        self.assertNotIn("b", members)
        self.assertNotIn("c", members)


class SingleClusterTest(unittest.TestCase):
    def test_all_items_in_one_cluster(self) -> None:
        result = _single_cluster([("a", "甲"), ("b", "乙")], None, "测试降级")
        self.assertEqual(len(result), 1)
        self.assertEqual(result.assignments, {"a": "cluster_0", "b": "cluster_0"})
        self.assertIn("测试降级", result.degraded_reason)


class LabelClustersTest(unittest.TestCase):
    class FakeLabelClient:
        def __init__(self, labels: list[tuple[int, str, str]]) -> None:
            self._labels = labels

        async def generate_object(self, prompt, schema, system=None):
            from engines.analytics.schemas import ClusterLabel, ClusterLabelling

            return ClusterLabelling(clusters=[
                ClusterLabel(index=i, label=l, summary=s) for i, l, s in self._labels])

    def _result(self) -> ClusteringResult:
        return ClusteringResult(clusters=[
            ClusterInfo("cluster_0", "手机太热了", 3, ("a", "b", "c"), ("a",)),
            ClusterInfo("cluster_1", "价格太贵", 2, ("d", "e"), ("d",)),
        ], assignments={})

    def test_labels_replaced_by_llm(self) -> None:
        client = self.FakeLabelClient([(0, "质疑散热宣传", "认为散热与宣传不符"),
                                       (1, "抱怨定价过高", "觉得价格超出承受")])
        result = asyncio.run(label_clusters(self._result(), {}, client=client))
        self.assertEqual(result.clusters[0].label, "质疑散热宣传")
        self.assertEqual(result.clusters[1].summary, "觉得价格超出承受")

    def test_keeps_default_label_on_failure(self) -> None:
        class BrokenClient:
            async def generate_object(self, *args, **kwargs):
                raise RuntimeError("模型不可用")

        result = asyncio.run(label_clusters(self._result(), {}, client=BrokenClient()))
        self.assertEqual(result.clusters[0].label, "手机太热了")   # 保留默认

    def test_missing_index_keeps_default(self) -> None:
        client = self.FakeLabelClient([(0, "只给了第一簇", "")])
        result = asyncio.run(label_clusters(self._result(), {}, client=client))
        self.assertEqual(result.clusters[0].label, "只给了第一簇")
        self.assertEqual(result.clusters[1].label, "价格太贵")

    def test_labels_without_index_align_by_position(self) -> None:
        """模型**经常省略 index**——它认为数组顺序已经表达了对应关系。

        实测中这一省略让整批结果校验失败，且失败是静默的：
        聚类看着成功，标签却退回了原文截断。索引字段必须可选并按位置兜底。
        """
        class NoIndexClient:
            async def generate_object(self, prompt, schema, system=None):
                from engines.analytics.schemas import ClusterLabel, ClusterLabelling

                return ClusterLabelling(clusters=[
                    ClusterLabel(label="第一簇标签"),
                    ClusterLabel(label="第二簇标签"),
                ])

        result = asyncio.run(label_clusters(self._result(), {}, client=NoIndexClient()))
        self.assertEqual(result.clusters[0].label, "第一簇标签")
        self.assertEqual(result.clusters[1].label, "第二簇标签")

    def test_cluster_label_index_is_optional(self) -> None:
        from engines.analytics.schemas import ClusterLabel

        self.assertIsNone(ClusterLabel(label="无索引").index)

    def test_empty_result_unchanged(self) -> None:
        empty = ClusteringResult()
        self.assertIs(asyncio.run(label_clusters(empty, {})), empty)


# ---------------------------------------------------------------------------
# 实体抽取
# ---------------------------------------------------------------------------

class FakeEntityClient:
    def __init__(self, mapping: dict[str, list[tuple[str, str]]],
                 error: Exception | None = None) -> None:
        self._mapping = mapping
        self._error = error
        self.calls = 0

    async def generate_object(self, prompt, schema, system=None):
        self.calls += 1
        if self._error:
            raise self._error
        for keyword, entities in self._mapping.items():
            if keyword in prompt:
                return EntityExtraction(entities=[
                    ExtractedEntity(name=n, entity_type=t) for n, t in entities])
        return EntityExtraction()


def make_content(uid: str, text: str) -> UnifiedContent:
    return UnifiedContent(
        content_uid=uid, source_type=SourceType.SOCIAL, source_table="weibo_note",
        business_id=abs(hash(uid)) % 100000,
        author=Author(account_id="u1", nickname="测试"), text=text)


class EntityIdTest(unittest.TestCase):
    def test_id_is_stable(self) -> None:
        """同一实体反复抽取必须得到同一 ID，否则图谱会散成孤立节点。"""
        self.assertEqual(make_entity_id("星耀科技", "机构"),
                         make_entity_id("星耀科技", "机构"))

    def test_same_name_different_type_differs(self) -> None:
        self.assertNotEqual(make_entity_id("星耀", "机构"), make_entity_id("星耀", "产品"))

    def test_relation_id_order_independent(self) -> None:
        """A→B 与 B→A 是同一条关系，ID 必须相同。"""
        self.assertEqual(make_relation_id("a", "b", RELATION_COOCCURRENCE),
                         make_relation_id("b", "a", RELATION_COOCCURRENCE))

    def test_relation_id_differs_by_type(self) -> None:
        self.assertNotEqual(make_relation_id("a", "b", "共现"),
                            make_relation_id("a", "b", "回应"))


class ExtractEntitiesTest(unittest.TestCase):
    def test_extracts_and_builds_cooccurrence(self) -> None:
        client = FakeEntityClient({
            "发热": [("星耀科技", "机构"), ("X-Phone 15", "产品")],
            "卫生": [("味鲜居", "机构"), ("市场监管部门", "机构")],
        })
        contents = [make_content("u1", "发热问题"), make_content("u2", "卫生问题")]
        result = asyncio.run(extract_entities(contents, client=client))

        self.assertEqual(result.entity_count, 4)
        self.assertEqual(result.relation_count, 2)     # 每条内容内部各一对
        self.assertEqual(len(result.per_content["u1"]), 2)

    def test_duplicate_mention_counted_once(self) -> None:
        """同一实体的重复出现不该产生自环关系。"""
        client = FakeEntityClient({"文本": [("星耀科技", "机构"), ("星耀科技", "机构")]})
        result = asyncio.run(extract_entities([make_content("u1", "文本")], client=client))
        self.assertEqual(result.entity_count, 1)
        self.assertEqual(len(result.per_content["u1"]), 1)
        self.assertEqual(result.relation_count, 0)

    def test_single_entity_no_relation(self) -> None:
        client = FakeEntityClient({"文本": [("星耀科技", "机构")]})
        result = asyncio.run(extract_entities([make_content("u1", "文本")], client=client))
        self.assertEqual(result.relation_count, 0)

    def test_no_entities(self) -> None:
        client = FakeEntityClient({"文本": []})
        result = asyncio.run(extract_entities([make_content("u1", "文本")], client=client))
        self.assertEqual(result.entity_count, 0)
        self.assertEqual(result.per_content["u1"], [])

    def test_failure_does_not_break_batch(self) -> None:
        client = FakeEntityClient({}, error=RuntimeError("模型超时"))
        contents = [make_content(f"u{i}", "文本") for i in range(3)]
        result = asyncio.run(extract_entities(contents, client=client))
        self.assertEqual(len(result.failed), 3)
        self.assertEqual(result.entity_count, 0)

    def test_top_entities_ranked_by_frequency(self) -> None:
        client = FakeEntityClient({})
        contents = [make_content(f"u{i}", "文本") for i in range(3)]

        class CountingClient:
            async def generate_object(self, prompt, schema, system=None):
                return EntityExtraction(entities=[
                    ExtractedEntity(name="星耀科技", entity_type="机构"),
                    ExtractedEntity(name="X-Phone 15", entity_type="产品")])

        result = asyncio.run(extract_entities(contents, client=CountingClient()))
        top = result.top_entities(limit=2)
        self.assertEqual(top[0][1], 3)          # 每条内容都出现
        self.assertEqual(top[0][0].name, "星耀科技")

    def test_types_distribution(self) -> None:
        client = FakeEntityClient({
            "文本": [("星耀科技", "机构"), ("X-Phone 15", "产品"), ("杭州", "地点")]})
        result = asyncio.run(extract_entities([make_content("u1", "文本")], client=client))
        self.assertEqual(result.types(), {"机构": 1, "产品": 1, "地点": 1})

    def test_empty_input(self) -> None:
        result = asyncio.run(extract_entities([]))
        self.assertEqual(result.entity_count, 0)


class ScopeIdTest(unittest.TestCase):
    def test_scoped_cluster_id_is_unique_per_scope(self) -> None:
        """聚类产出的 cluster_0 在不同主题间必然重复，落库前必须加作用域。"""
        self.assertNotEqual(scoped_cluster_id("topicA", "cluster_0"),
                            scoped_cluster_id("topicB", "cluster_0"))
        self.assertEqual(scoped_cluster_id("topicA", "cluster_0"), "topicA:cluster_0")

    def test_event_id_derived_from_scope(self) -> None:
        self.assertEqual(make_event_id("星耀XPhone15"), "evt_星耀XPhone15")


class ExtractionResultTest(unittest.TestCase):
    def test_edge_creation(self) -> None:
        edge = EntityRelationEdge(
            relation_id="rel_1", from_entity_id="a", to_entity_id="b", weight=2.0)
        self.assertEqual(edge.relation_type, RELATION_COOCCURRENCE)
        self.assertEqual(edge.weight, 2.0)

    def test_empty_result_counts(self) -> None:
        result = ExtractionResult()
        self.assertEqual(result.entity_count, 0)
        self.assertEqual(result.relation_count, 0)


if __name__ == "__main__":
    unittest.main()
