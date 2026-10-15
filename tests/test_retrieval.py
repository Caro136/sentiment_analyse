"""向量检索与 RRF 融合测试。

RRF 是纯函数，重点覆盖其排序语义；Milvus 相关的部分放在集成测试里，
服务不可用时跳过——单元测试不该依赖外部服务。
"""

import asyncio
import unittest

from engines.analytics.indexing import index_contents
from engines.analytics.retrieval import (
    CHANNEL_KEYWORD,
    CHANNEL_VECTOR,
    fuse_hits,
    reciprocal_rank_fusion,
)
from engines.analytics.vector_store import CollectionStats, SearchHit
from engines.contracts.content import Author, UnifiedContent
from engines.contracts.enums import SourceType


class RRFTest(unittest.TestCase):
    def test_single_channel_preserves_order(self) -> None:
        result = reciprocal_rank_fusion({"a": ["x", "y", "z"]})
        self.assertEqual([r[0] for r in result], ["x", "y", "z"])

    def test_scores_decrease_with_rank(self) -> None:
        scores = [r[1] for r in reciprocal_rank_fusion({"a": ["x", "y", "z"]})]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_multi_channel_boosts_overlapping_candidates(self) -> None:
        """两个通道都命中的条目分数更高——这是 RRF 的核心价值。"""
        result = reciprocal_rank_fusion({"a": ["x", "y"], "b": ["y"]})
        by_uid = {r[0]: r[1] for r in result}
        self.assertGreater(by_uid["y"], by_uid["x"])

    def test_ranks_recorded_per_channel(self) -> None:
        ranks = {r[0]: r[2] for r in reciprocal_rank_fusion(
            {"a": ["x", "y"], "b": ["y", "x"]})}
        self.assertEqual(ranks["x"], {"a": 1, "b": 2})
        self.assertEqual(ranks["y"], {"a": 2, "b": 1})

    def test_empty_input(self) -> None:
        self.assertEqual(reciprocal_rank_fusion({}), [])

    def test_k_controls_score_spread(self) -> None:
        """k 越小，头部与尾部的分差越大。

        k 太大（如 1000）会让所有排名几乎同分，融合失去区分度。
        """
        small = reciprocal_rank_fusion({"a": ["x", "y"]}, k=1)
        large = reciprocal_rank_fusion({"a": ["x", "y"]}, k=1000)
        self.assertGreater(small[0][1] - small[1][1], large[0][1] - large[1][1])


class FuseHitsTest(unittest.TestCase):
    def _hit(self, uid: str, content: str = "内容") -> SearchHit:
        return SearchHit(content_uid=uid, score=0.9, content=content,
                         source_type="social_media")

    def test_overlap_ranks_highest(self) -> None:
        result = fuse_hits(["k1", "k2"], [self._hit("v1"), self._hit("k1")])
        self.assertEqual(result[0].content_uid, "k1")

    def test_vector_only_results_kept(self) -> None:
        result = fuse_hits([], [self._hit("v1")])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].channels, (CHANNEL_VECTOR,))

    def test_keyword_only_results_kept(self) -> None:
        result = fuse_hits(["k1"], [])
        self.assertEqual(result[0].channels, (CHANNEL_KEYWORD,))

    def test_both_empty(self) -> None:
        self.assertEqual(fuse_hits([], []), [])

    def test_limit_applied(self) -> None:
        hits = [self._hit(f"v{i}") for i in range(10)]
        self.assertEqual(len(fuse_hits([], hits, limit=3)), 3)

    def test_vector_payload_preserved(self) -> None:
        """融合后保留正文——上层渲染证据块要用，再查一次库是浪费。"""
        result = fuse_hits([], [self._hit("v1", content="发热严重")])
        self.assertEqual(result[0].content, "发热严重")

    def test_channel_count(self) -> None:
        result = fuse_hits(["k1"], [self._hit("k1")])
        self.assertEqual(result[0].channel_count, 2)

    def test_ranks_available_on_hit(self) -> None:
        result = fuse_hits(["k1"], [self._hit("k1")])
        self.assertEqual(set(result[0].ranks), {CHANNEL_KEYWORD, CHANNEL_VECTOR})


# ---------------------------------------------------------------------------
# 索引同步（用替身，不连 Milvus）
# ---------------------------------------------------------------------------

class FakeEmbedder:
    def __init__(self, dim: int = 4, error: Exception | None = None) -> None:
        self._dim = dim
        self._error = error

    async def embed(self, texts):
        if self._error:
            raise self._error
        return [[0.1] * self._dim for _ in texts]

    async def embed_one(self, text):
        return [0.1] * self._dim


class FakeStore:
    def __init__(self, dimension: int = 0) -> None:
        self.dimension = dimension
        self.records: list = []
        self.dropped = False

    def ensure_collection(self, dimension: int) -> bool:
        if not self.dimension:
            self.dimension = dimension
            return True
        return False

    def stats(self) -> CollectionStats:
        return CollectionStats(name="fake", exists=True, dimension=self.dimension)

    def upsert(self, records) -> int:
        self.records.extend(records)
        return len(records)

    def drop_collection(self) -> None:
        self.dropped = True
        self.dimension = 0


def make_content(uid: str, text: str = "测试内容", title: str = "") -> UnifiedContent:
    return UnifiedContent(
        content_uid=uid, source_type=SourceType.SOCIAL, source_table="weibo_note",
        business_id=abs(hash(uid)) % 100000,
        author=Author(account_id="u1", nickname="测试"),
        text=text, title=title, published_at=1700000000)


class IndexingTest(unittest.TestCase):
    def test_indexes_usable_contents(self) -> None:
        contents = [make_content(f"u{i}") for i in range(3)]
        store = FakeStore()
        result = asyncio.run(index_contents(contents, store=store,
                                            embedder=FakeEmbedder()))
        self.assertTrue(result.ok)
        self.assertEqual(result.indexed, 3)
        self.assertEqual(len(store.records), 3)

    def test_empty_text_skipped(self) -> None:
        """空文本不该入库——编码空串没意义，还会污染检索结果。"""
        contents = [make_content("a", "有内容"), make_content("b", "   ")]
        store = FakeStore()
        result = asyncio.run(index_contents(contents, store=store,
                                            embedder=FakeEmbedder()))
        self.assertEqual(result.indexed, 1)

    def test_all_empty_reports_error(self) -> None:
        result = asyncio.run(index_contents([make_content("a", "  ")], store=FakeStore(),
                                            embedder=FakeEmbedder()))
        self.assertFalse(result.ok)
        self.assertIn("空", result.error)

    def test_encoding_failure_reported(self) -> None:
        result = asyncio.run(index_contents(
            [make_content("a")], store=FakeStore(),
            embedder=FakeEmbedder(error=RuntimeError("服务不可用"))))
        self.assertFalse(result.ok)
        self.assertIn("编码失败", result.error)

    def test_dimension_mismatch_reports_actionable_error(self) -> None:
        """换过编码模型时集合维度不会自动变，报错要能指导修复。"""
        store = FakeStore(dimension=8)
        result = asyncio.run(index_contents(
            [make_content("a")], store=store, embedder=FakeEmbedder(dim=4)))
        self.assertFalse(result.ok)
        self.assertIn("不一致", result.error)
        self.assertIn("重建集合", result.error)

    def test_dimension_mismatch_can_recreate(self) -> None:
        store = FakeStore(dimension=8)
        result = asyncio.run(index_contents(
            [make_content("a")], store=store, embedder=FakeEmbedder(dim=4),
            recreate_on_mismatch=True))
        self.assertTrue(result.ok)
        self.assertTrue(store.dropped)
        self.assertEqual(store.dimension, 4)

    def test_title_included_in_index_text(self) -> None:
        """标题常含事件主体，只编码正文会丢掉这部分信号。"""
        content = make_content("a", text="正文内容", title="X-Phone 15 发热争议")
        store = FakeStore()
        asyncio.run(index_contents([content], store=store, embedder=FakeEmbedder()))
        self.assertIn("X-Phone 15 发热争议", store.records[0].content)

    def test_record_carries_metadata(self) -> None:
        content = make_content("a", text="内容")
        store = FakeStore()
        asyncio.run(index_contents([content], store=store, embedder=FakeEmbedder()))
        record = store.records[0]
        self.assertEqual(record.content_uid, "a")
        self.assertEqual(record.source_type, "social_media")
        self.assertEqual(record.published_at, 1700000000)


if __name__ == "__main__":
    unittest.main()
