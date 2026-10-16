"""监测→分析链路编排的测试（T-34）。

**全程离线**：六个阶段都是外部依赖（数据库、模型、向量库），测试把它们换成替身，
只验证编排本身——顺序、参数传递、失败处理、统计汇总。
这类测试的价值在于：编排写错不会报错，只会静默少跑一步
（例如聚类忘了带情感标签、事件没关联内容），而那正是「大屏有数、报告没数」的来源。
"""

import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from app.services.pipeline.run import PipelineReport, resolve_topic, run_pipeline
from engines.analytics.clustering import ClusteringResult, ClusterInfo
from engines.analytics.entities import ExtractionResult, EntityNode, EntityRelationEdge
from engines.analytics.indexing import IndexResult
from engines.analytics.service import AnalysisResult, AnalysisStats
from engines.common.runtime.concurrency import DEFAULT_LIMIT
from engines.contracts.content import Author, Engagement, UnifiedContent
from engines.contracts.enums import SourceType
from tests.fakes import silence_app_logging


class FakeTopic:
    def __init__(self, topic_id="tp1", name="味鲜居后厨卫生", status="enabled",
                 source_keyword="味鲜居后厨"):
        self.topic_id = topic_id
        self.name = name
        self.status = status
        self.source_keyword = source_keyword


def make_content(uid: str) -> UnifiedContent:
    return UnifiedContent(
        content_uid=uid,
        source_type=SourceType.SOCIAL,
        source_table="weibo_note",
        business_id=abs(hash(uid)) % 100000,
        author=Author(account_id="u1", nickname="测试"),
        text=f"内容 {uid}",
        engagement=Engagement(likes=10),
        published_at=1700000000,
    )


def make_ingestion(contents):
    from engines.ingestion.pipeline import IngestionResult, IngestionStats

    return IngestionResult(
        contents=contents,
        fingerprints={c.content_uid: 12345 for c in contents},
        stats=IngestionStats(fetched_contents=len(contents), unique_count=len(contents)),
    )


def make_analysis(contents, failed=()):
    return AnalysisResult(
        labels={c.content_uid: object() for c in contents if c.content_uid not in failed},
        stats=AnalysisStats(total=len(contents), succeeded=len(contents) - len(failed),
                            failed=len(failed)),
        failed_uids=list(failed),
    )


def make_clusters() -> ClusteringResult:
    return ClusteringResult(clusters=[
        ClusterInfo(cluster_id="cluster_0", label="要求退款", size=1,
                    member_ids=("uid0",), representative_ids=("uid0",),
                    dominant_sentiment="negative"),
        ClusterInfo(cluster_id="cluster_1", label="观望", size=1,
                    member_ids=("uid1",), representative_ids=("uid1",),
                    dominant_sentiment="neutral"),
    ])


class PipelineHarness(unittest.IsolatedAsyncioTestCase):
    """把六个阶段的替身与控制点装在一起的骨架。"""

    def setUp(self) -> None:
        logging_patcher = silence_app_logging()
        logging_patcher.start()
        self.addCleanup(logging_patcher.stop)

        self.topic = FakeTopic()
        self.calls: dict[str, object] = {}
        self.contents = [make_content("uid0"), make_content("uid1")]

        self.ingestion = make_ingestion(self.contents)
        self.analysis = make_analysis(self.contents)
        self.extraction = ExtractionResult(
            entities=[EntityNode("ent1", "味鲜居", "机构")],
            relations=[EntityRelationEdge("rel1", "ent1", "ent2", "共现", 1.0)],
        )
        self.clusters = make_clusters()
        self.index_result = IndexResult(requested=2, indexed=2, dimension=768, duration=0.5)

        self.patches = [
            patch("app.services.pipeline.run.ingest", self._fake_ingest),
            patch("app.services.pipeline.run.analyze_contents", self._fake_analyze),
            patch("app.services.pipeline.run.extract_entities", self._fake_extract),
            patch("app.services.pipeline.run.cluster_texts", self._fake_cluster),
            patch("app.services.pipeline.run.index_contents", self._fake_index),
            patch("app.services.pipeline.run.session_scope", _fake_session_scope),
            patch("app.services.pipeline.run.SqlMonitoringRepository",
                  _FakeMonitoringRepository(lambda: self.topic)),
            patch("app.services.pipeline.run.analytics_repo.ensure_event", self._fake_ensure_event),
            patch("app.services.pipeline.run.analytics_repo.attach_contents", self._fake_attach),
            patch("app.services.pipeline.run.analytics_repo.fetch_labels", self._fake_labels),
            patch("app.services.pipeline.run.analytics_repo.save_entities", self._fake_save_entities),
            patch("app.services.pipeline.run.analytics_repo.save_clusters", self._fake_save_clusters),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _fake_ingest(self, query, **kwargs):
        self.calls["ingest"] = {"query": query, **kwargs}
        return self.ingestion

    async def _fake_analyze(self, contents, **kwargs):
        self.calls["analyze"] = {"contents": list(contents), **kwargs}
        return self.analysis

    async def _fake_extract(self, contents, **kwargs):
        self.calls["extract"] = list(contents)
        return self.extraction

    async def _fake_cluster(self, items, **kwargs):
        self.calls["cluster"] = {"items": list(items), **kwargs}
        return self.clusters

    async def _fake_index(self, contents, **kwargs):
        self.calls["index"] = list(contents)
        return self.index_result

    async def _fake_ensure_event(self, session, scope, **kwargs):
        self.calls["ensure_event"] = {"scope": scope, **kwargs}
        return "evt_1"

    async def _fake_attach(self, session, event_id, uids):
        self.calls["attach"] = {"event_id": event_id, "uids": list(uids)}
        return len(uids)

    async def _fake_labels(self, session, uids):
        return {uid: {"sentiment": "negative"} for uid in uids}

    async def _fake_save_entities(self, session, extraction, **kwargs):
        self.calls["save_entities"] = kwargs
        return len(extraction.entities), len(extraction.relations)

    async def _fake_save_clusters(self, session, clusters, **kwargs):
        self.calls["save_clusters"] = {"clusters": list(clusters), **kwargs}
        return len(clusters)

    async def run_pipeline(self, **kwargs) -> PipelineReport:
        return await run_pipeline(topic_name="味鲜居后厨卫生", **kwargs)


@asynccontextmanager
async def _fake_session_scope():
    """替身会话：真正的 SQL 由被替身的仓储函数屏蔽掉。"""
    yield object()


class _FakeMonitoringRepository:
    """主题仓储替身。

    `topic_getter` 是个可调用对象而不是主题本身：用例会在 setUp 之后替换
    `self.topic`（测停用、测缺关键词），patch 装的是这个类，取主题必须发生在调用时。
    """

    def __init__(self, topic_getter, found: bool = True) -> None:
        self._topic_getter = topic_getter
        self._found = found

    def __call__(self, session) -> "_FakeMonitoringRepository":
        return self

    async def get_topic(self, topic_or_id):
        # 真实实现按 ID 查：给的是主题名时应当查不到，从而走名称匹配分支
        if not self._found or not str(topic_or_id).startswith("tp"):
            return None
        return self._topic_getter()

    async def list_topics(self, **kwargs):
        topic = self._topic_getter()
        return (1, [topic]) if self._found else (0, [])


class PipelineHappyPathTest(PipelineHarness):
    """正常路径：六个阶段按序执行、参数正确、统计汇总。"""

    async def test_all_stages_run_in_order(self) -> None:
        report = await self.run_pipeline()

        # 接入用的是**主题关键词**（不是主题名）：归属匹配靠它
        self.assertEqual(self.calls["ingest"]["query"].keyword, "味鲜居后厨")
        # 打标带上了主题与去重/水军信息——漏了这些，标签表就没有主题归属
        self.assertEqual(self.calls["analyze"]["topic_id"], "tp1")
        self.assertEqual(self.calls["analyze"]["fingerprints"], {"uid0": 12345, "uid1": 12345})
        self.assertEqual(self.calls["analyze"]["bot_account_ids"], set())
        self.assertEqual(self.calls["analyze"]["max_concurrency"], DEFAULT_LIMIT)

        # 事件按主题关键词派生，内容全部关联
        self.assertEqual(self.calls["ensure_event"]["scope"], "味鲜居后厨")
        self.assertEqual(self.calls["attach"]["uids"], ["uid0", "uid1"])
        # 标题必须与主题名同步：三方产物按主题词命名，而「从事件发起报告」用的是事件标题，
        # 两者不一致时报告只会以「产物尚未生成」失败（T-34 联调踩到）
        self.assertTrue(self.calls["ensure_event"]["sync_title"])
        self.assertEqual(self.calls["ensure_event"]["title"], "味鲜居后厨卫生")

        # 聚类消费打标产出的情感（否则簇的 dominant_sentiment 恒为空）
        self.assertEqual(self.calls["cluster"]["sentiments"],
                         {"uid0": "negative", "uid1": "negative"})
        self.assertEqual([uid for uid, _ in self.calls["cluster"]["items"]], ["uid0", "uid1"])

        # 簇与实体都挂到同一个事件与主题上
        self.assertEqual(self.calls["save_clusters"]["scope"], "味鲜居后厨")
        self.assertEqual(self.calls["save_clusters"]["event_id"], "evt_1")
        self.assertEqual(self.calls["save_clusters"]["topic_id"], "tp1")
        self.assertEqual(self.calls["save_entities"], {"event_id": "evt_1"})

        self.assertEqual(len(self.calls["index"]), 2)

    async def test_report_summarises_every_stage(self) -> None:
        report = await self.run_pipeline()

        self.assertTrue(report.ok)
        self.assertEqual(report.event_id, "evt_1")
        self.assertEqual(report.attached, 2)
        self.assertEqual(report.cluster_count, 2)
        self.assertEqual(report.entity_count, 1)
        self.assertEqual(report.relation_count, 1)
        self.assertEqual(report.indexed.indexed, 2)
        self.assertEqual(report.warnings, [])

        text = report.render()
        self.assertIn("味鲜居后厨卫生", text)
        self.assertIn("观点簇 2 个", text)
        self.assertIn("实体 1 个 / 关系 1 条", text)

    async def test_window_hours_becomes_start_time(self) -> None:
        import time

        before = int(time.time()) - 72 * 3600
        await self.run_pipeline(window_hours=72)
        start = self.calls["ingest"]["query"].start_time
        self.assertGreaterEqual(start, before - 2)
        self.assertLessEqual(start, int(time.time()) - 72 * 3600 + 2)


class PipelineFailureHandlingTest(PipelineHarness):
    """局部失败不阻断全局，但必须进 warnings。"""

    async def test_empty_ingestion_short_circuits(self) -> None:
        self.ingestion = make_ingestion([])

        report = await self.run_pipeline()

        self.assertFalse(report.ok)
        self.assertIn("ingest", self.calls)
        self.assertNotIn("analyze", self.calls)     # 没有输入，不该继续
        self.assertNotIn("cluster", self.calls)
        self.assertTrue(any("没有取到任何内容" in w for w in report.warnings))

    async def test_index_failure_is_a_warning_not_a_failure(self) -> None:
        """向量库不可用时链路仍算跑通：研判会降级为关键词召回。"""
        self.index_result = IndexResult(requested=2, indexed=0, error="连接 Milvus 失败")

        report = await self.run_pipeline()

        self.assertTrue(report.ok)
        self.assertTrue(any("向量索引失败" in w for w in report.warnings))
        self.assertIn("连接 Milvus 失败", report.render())

    async def test_degraded_clustering_is_recorded(self) -> None:
        self.clusters = ClusteringResult(degraded_reason="样本量不足，未做聚类",
                                         clusters=[])

        report = await self.run_pipeline()

        self.assertEqual(report.cluster_count, 0)
        self.assertEqual(report.cluster_degraded, "样本量不足，未做聚类")
        self.assertTrue(report.ok)

    async def test_analysis_failures_are_reported(self) -> None:
        self.analysis = make_analysis(self.contents, failed=("uid1",))

        report = await self.run_pipeline()

        self.assertTrue(any("打标失败" in w for w in report.warnings))
        # 失败的内容照样关联到事件：打标失败不代表内容不该出现在时间线里
        self.assertEqual(self.calls["attach"]["uids"], ["uid0", "uid1"])

    async def test_skipping_stages(self) -> None:
        await self.run_pipeline(with_entities=False, with_clusters=False, with_index=False)

        self.assertNotIn("extract", self.calls)
        self.assertNotIn("cluster", self.calls)
        self.assertNotIn("index", self.calls)
        self.assertNotIn("save_entities", self.calls)
        self.assertNotIn("save_clusters", self.calls)


class ResolveTopicTest(PipelineHarness):
    """目标主题的解析与拒绝路径。"""

    async def test_disabled_topic_is_refused_without_force(self) -> None:
        self.topic = FakeTopic(status="disabled")

        with self.assertRaises(ValueError) as ctx:
            await self.run_pipeline()

        self.assertIn("已停用", str(ctx.exception))
        self.assertNotIn("ingest", self.calls)

    async def test_force_allows_disabled_topic(self) -> None:
        self.topic = FakeTopic(status="disabled")

        report = await self.run_pipeline(force=True)

        self.assertTrue(report.ok)
        self.assertIn("ingest", self.calls)

    async def test_topic_without_source_keyword_is_refused(self) -> None:
        """没有主题关键词的主题接不到任何内容——明确报错，而不是静默跑出空结果。"""
        self.topic = FakeTopic(source_keyword=None)

        with self.assertRaises(ValueError) as ctx:
            await self.run_pipeline()

        self.assertIn("没有主题关键词", str(ctx.exception))

    async def test_unknown_topic_name_raises(self) -> None:
        with patch("app.services.pipeline.run.SqlMonitoringRepository",
                   _FakeMonitoringRepository(lambda: self.topic, found=False)):
            with self.assertRaises(ValueError) as ctx:
                await self.run_pipeline()
        self.assertIn("主题不存在", str(ctx.exception))

    async def test_keyword_only_run_without_topic(self) -> None:
        """不绑定主题也能跑（临时试跑）：此时事件与标签都没有主题归属。"""
        report = await run_pipeline(keyword="临时关键词", with_index=False)

        self.assertEqual(self.calls["ingest"]["query"].keyword, "临时关键词")
        self.assertIsNone(self.calls["analyze"]["topic_id"])
        self.assertIsNone(self.calls["save_clusters"]["topic_id"])
        self.assertIsNone(report.topic_id)
        self.assertTrue(report.ok)

    async def test_resolve_topic_prefers_source_keyword(self) -> None:
        topic, keyword = await resolve_topic(topic_name="味鲜居后厨卫生")
        self.assertEqual(topic.name, "味鲜居后厨卫生")
        self.assertEqual(keyword, "味鲜居后厨")

    async def test_topic_can_be_given_as_id(self) -> None:
        """`--topic` 同时接受 ID 与名称：先按 ID 查，查不到再按名称匹配。"""
        topic, keyword = await resolve_topic(topic_name="tp1")

        self.assertEqual(topic.topic_id, "tp1")
        self.assertEqual(keyword, "味鲜居后厨")


if __name__ == "__main__":
    unittest.main()
