"""事件与溯源用例测试（FR-45~FR-48）。

重点在两处「算法」而不是接口：

1. **溯源角色推导**（`derive_roles`）：首发/引爆/回应/跟进的判定直接决定
   溯源图对不对，而它全部由内容属性推出来，错了不会有任何异常；
2. **事件统计聚合**（`collect_stats`）：声量、负面率、最高风险、时间范围
   都是现算的（`event` 表的聚合列是空的），口径错了会一路带到大盘与报告。

另外验证聚类按规模倒序、成员下钻返回与动态流一致的结构。
"""

import unittest

from app.errors import AppError
from app.services.event import service
from engines.contracts.enums import ContentRole, SourceType
from tests.fakes import (
    InMemoryContentRepository,
    InMemoryEventRepository,
    make_analysis,
    make_cluster,
    make_content,
    make_event,
    patch_raw_layer,
)

WEIBO_ORIGIN = "social_media:weibo_note:1"
WEIBO_KOL = "social_media:weibo_note:2"
WEIBO_COMMENT = "social_media:weibo_note_comment:3"
NEWS_1 = "news_portal:news_article:11"
OFFICIAL_1 = "social_media:weibo_note:4"
FORUM_1 = "industry_forum:forum_thread:21"


class RoleDerivationTest(unittest.TestCase):
    """`derive_roles` 是纯函数，直接构造内容断言。"""

    def test_origin_is_earliest_post(self) -> None:
        contents = [make_content(WEIBO_KOL, published_at=200),
                    make_content(WEIBO_ORIGIN, published_at=100)]
        roles = service.derive_roles(contents)
        self.assertIs(roles[WEIBO_ORIGIN], ContentRole.ORIGIN)

    def test_comment_never_becomes_origin(self) -> None:
        """评论再早也不是事件起点——它是内容的下游反应。"""
        contents = [make_content(WEIBO_COMMENT, published_at=50, record_type="comment"),
                    make_content(WEIBO_ORIGIN, published_at=100)]
        roles = service.derive_roles(contents)

        self.assertIs(roles[WEIBO_COMMENT], ContentRole.SPREAD)
        self.assertIs(roles[WEIBO_ORIGIN], ContentRole.ORIGIN)

    def test_ignition_is_highest_influence_kol(self) -> None:
        contents = [make_content(WEIBO_ORIGIN, published_at=100, influence_score=20),
                    make_content(WEIBO_KOL, published_at=300, influence_score=95,
                                 verified_type="黄V"),
                    make_content(FORUM_1, published_at=400, influence_score=40)]
        roles = service.derive_roles(contents)
        self.assertIs(roles[WEIBO_KOL], ContentRole.IGNITION)

    def test_no_kol_means_no_ignition(self) -> None:
        """没有达到 KOL 阈值的账号时不硬凑一个引爆节点。"""
        contents = [make_content(WEIBO_ORIGIN, published_at=100, influence_score=20),
                    make_content(WEIBO_KOL, published_at=300, influence_score=30)]
        roles = service.derive_roles(contents)

        self.assertNotIn(ContentRole.IGNITION, set(roles.values()))

    def test_official_account_is_marked(self) -> None:
        contents = [make_content(WEIBO_ORIGIN, published_at=100),
                    make_content(OFFICIAL_1, published_at=500, verified_type="官方",
                                 influence_score=92)]
        roles = service.derive_roles(contents)

        self.assertIs(roles[OFFICIAL_1], ContentRole.OFFICIAL)
        self.assertIs(roles[WEIBO_ORIGIN], ContentRole.ORIGIN)

    def test_official_beats_ignition_when_influence_highest(self) -> None:
        """官方账号影响力通常最高，但不该被算成「引爆」（它是回应方）。"""
        contents = [make_content(WEIBO_ORIGIN, published_at=100),
                    make_content(OFFICIAL_1, published_at=200, verified_type="政务",
                                 influence_score=99),
                    make_content(WEIBO_KOL, published_at=300, influence_score=80,
                                 verified_type="黄V")]
        roles = service.derive_roles(contents)

        self.assertIs(roles[OFFICIAL_1], ContentRole.OFFICIAL)
        self.assertIs(roles[WEIBO_KOL], ContentRole.IGNITION)

    def test_news_and_media_are_followup(self) -> None:
        contents = [make_content(WEIBO_ORIGIN, published_at=100),
                    make_content(NEWS_1, published_at=200),
                    make_content(WEIBO_KOL, published_at=300, verified_type="媒体")]
        roles = service.derive_roles(contents)

        self.assertIs(roles[NEWS_1], ContentRole.FOLLOWUP)
        self.assertIs(roles[WEIBO_KOL], ContentRole.FOLLOWUP)

    def test_everything_else_is_spread(self) -> None:
        contents = [make_content(WEIBO_ORIGIN, published_at=100),
                    make_content(FORUM_1, published_at=400, influence_score=40)]
        roles = service.derive_roles(contents)
        self.assertIs(roles[FORUM_1], ContentRole.SPREAD)

    def test_empty_input(self) -> None:
        self.assertEqual(service.derive_roles([]), {})

    def test_only_comments_degrades_gracefully(self) -> None:
        roles = service.derive_roles([make_content(WEIBO_COMMENT, record_type="comment")])
        self.assertIs(roles[WEIBO_COMMENT], ContentRole.SPREAD)


class StatsTest(unittest.TestCase):
    def test_collect_stats(self) -> None:
        contents = [make_content(WEIBO_ORIGIN, published_at=100, likes=10),
                    make_content(WEIBO_KOL, published_at=500, likes=90),
                    make_content(WEIBO_COMMENT, published_at=600, likes=5,
                                 record_type="comment")]
        records = [make_analysis(WEIBO_ORIGIN, sentiment="negative", risk_level="B",
                                 intensity_index=0.4),
                   make_analysis(WEIBO_KOL, sentiment="negative", risk_level="S",
                                 intensity_index=0.8),
                   make_analysis(WEIBO_COMMENT, sentiment="neutral", risk_level="C")]

        stats = service.collect_stats(contents, records)

        self.assertEqual(stats["content_count"], 3)
        self.assertEqual(stats["post_count"], 2)
        self.assertEqual(stats["total_volume"], 105)
        self.assertEqual(stats["negative_count"], 2)
        self.assertEqual(stats["negative_ratio"], 0.6667)
        self.assertEqual(stats["risk_level"], "S")          # 取最高风险
        self.assertEqual(stats["first_seen_at"], 100)
        self.assertEqual(stats["last_seen_at"], 600)
        self.assertEqual(stats["sentiment_distribution"],
                         {"positive": 0, "neutral": 1, "negative": 2})

    def test_empty_event_stats_are_null_not_zero(self) -> None:
        """没有内容时负面率与时间范围应为 null——0 会被误读成「统计过了，是零」。"""
        stats = service.collect_stats([], [])
        self.assertIsNone(stats["negative_ratio"])
        self.assertIsNone(stats["first_seen_at"])
        self.assertIsNone(stats["risk_level"])


class EventServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.event = make_event("evt_1", title="星耀XPhone15", status="monitoring")
        uids = [WEIBO_ORIGIN, WEIBO_KOL, NEWS_1, WEIBO_COMMENT]
        self.contents = {
            WEIBO_ORIGIN: make_content(WEIBO_ORIGIN, published_at=100, text="最早的一条"),
            WEIBO_KOL: make_content(WEIBO_KOL, published_at=300, influence_score=95,
                                    verified_type="黄V", text="引爆的一条", likes=900),
            NEWS_1: make_content(NEWS_1, published_at=200, text="媒体跟进"),
            WEIBO_COMMENT: make_content(WEIBO_COMMENT, published_at=400,
                                        record_type="comment", text="一条评论",
                                        likes=3),
        }
        self.records = InMemoryContentRepository(
            records=[make_analysis(uid, topic_id="tp1") for uid in uids],
            topics={"tp1": "星耀XPhone15"})
        self.repo = InMemoryEventRepository(
            events=[self.event], links={"evt_1": uids},
            clusters=[make_cluster("c1", size=30, label="大簇"),
                      make_cluster("c2", size=5, label="小簇")],
            members={"c1": [WEIBO_ORIGIN, WEIBO_KOL]},
            representatives={"c1": [WEIBO_KOL]},
            graph={"nodes": [{"id": "e1", "name": "星耀科技", "type": "机构"}],
                   "edges": [{"source": "e1", "target": "e2", "type": "推出",
                              "weight": 15.0}]},
            entity_counts=(2, 1))

    def raw_layer(self, **kwargs):
        kwargs.setdefault("hits", [])
        kwargs.setdefault("contents", self.contents)
        return patch_raw_layer(**kwargs)

    async def test_list_events_computes_stats(self) -> None:
        with self.raw_layer():
            total, items = await service.list_events(self.repo, self.records, session=None)

        self.assertEqual(total, 1)
        summary = items[0]
        self.assertEqual(summary["event_id"], "evt_1")
        self.assertEqual(summary["topic_id"], "tp1")              # 由内容归属推出
        self.assertEqual(summary["topic_name"], "星耀XPhone15")
        self.assertEqual(summary["cluster_count"], 2)
        self.assertEqual(summary["stats"]["content_count"], 4)
        self.assertEqual(summary["stats"]["last_seen_at"], 400)

    async def test_list_events_filters_by_computed_risk(self) -> None:
        """风险筛选筛的是现算的最高风险，与列表展示的值一致。"""
        self.records.records[WEIBO_KOL] = make_analysis(WEIBO_KOL, risk_level="S",
                                                        topic_id="tp1")
        with self.raw_layer():
            total_s, _ = await service.list_events(self.repo, self.records, session=None,
                                                   risk_level="S")
            total_c, _ = await service.list_events(self.repo, self.records, session=None,
                                                   risk_level="C")

        self.assertEqual((total_s, total_c), (1, 0))

    async def test_detail_includes_graph_scale(self) -> None:
        with self.raw_layer():
            detail = await service.get_event_detail(self.repo, self.records, session=None,
                                                    event_id="evt_1")

        self.assertEqual(detail["entity_count"], 2)
        self.assertEqual(detail["relation_count"], 1)

    async def test_missing_event_returns_404(self) -> None:
        with self.raw_layer():
            with self.assertRaises(AppError) as ctx:
                await service.get_event_detail(self.repo, self.records, session=None,
                                               event_id="evt-none")

        self.assertEqual(ctx.exception.code, "BIZ_201")

    async def test_timeline_is_time_ascending_with_roles(self) -> None:
        with self.raw_layer():
            timeline = await service.build_timeline(self.repo, self.records, session=None,
                                                    event_id="evt_1", node_scope="all")

        times = [node["published_at"] for node in timeline["nodes"]]
        self.assertEqual(times, sorted(times))                    # 正序
        by_uid = {node["content_uid"]: node for node in timeline["nodes"]}
        self.assertEqual(by_uid[WEIBO_ORIGIN]["role"], "origin")
        self.assertEqual(by_uid[WEIBO_KOL]["role"], "ignition")
        self.assertEqual(by_uid[NEWS_1]["role"], "followup")
        self.assertEqual(timeline["role_source"], "derived")
        self.assertEqual(timeline["total_contents"], 4)
        self.assertEqual(timeline["returned"], 4)

    async def test_timeline_key_scope_keeps_origin_and_ignition(self) -> None:
        """默认只给关键节点，但首发与引爆必须在里面，且确实做了裁剪。"""
        spread_uids = [f"social_media:weibo_note:{100 + i}" for i in range(40)]
        contents = dict(self.contents)
        contents.update({uid: make_content(uid, published_at=1000 + i, likes=i * 10)
                         for i, uid in enumerate(spread_uids)})
        repo = InMemoryEventRepository(
            events=[self.event],
            links={"evt_1": list(self.contents) + spread_uids},
            clusters=[], members={}, representatives={})

        with self.raw_layer(contents=contents):
            key_scope = await service.build_timeline(repo, self.records, session=None,
                                                     event_id="evt_1")
            full_scope = await service.build_timeline(repo, self.records, session=None,
                                                      event_id="evt_1", node_scope="all")

        uids = {node["content_uid"] for node in key_scope["nodes"]}
        self.assertIn(WEIBO_ORIGIN, uids)                    # 首发一定在
        self.assertIn(WEIBO_KOL, uids)                       # 引爆一定在
        self.assertLess(key_scope["returned"], key_scope["total_contents"])
        self.assertEqual(full_scope["returned"], full_scope["total_contents"])

    async def test_key_scope_caps_spread_nodes(self) -> None:
        """扩散节点按比例取且有上限：事件上千条内容时时间线不能把评论全铺开。"""
        spread_uids = [f"social_media:weibo_note_comment:{200 + i}" for i in range(400)]
        contents = {uid: make_content(uid, published_at=1000 + i, record_type="comment",
                                      likes=400 - i) for i, uid in enumerate(spread_uids)}
        contents[WEIBO_ORIGIN] = self.contents[WEIBO_ORIGIN]
        repo = InMemoryEventRepository(events=[self.event],
                                       links={"evt_1": [WEIBO_ORIGIN, *spread_uids]})

        with self.raw_layer(contents=contents):
            timeline = await service.build_timeline(repo, self.records, session=None,
                                                    event_id="evt_1")

        # 401 条内容里，关键节点 = 首发 1 + 扩散上限 50
        self.assertLessEqual(timeline["returned"], 1 + 50)
        self.assertGreater(timeline["returned"], 1)

    async def test_timeline_rejects_unknown_scope(self) -> None:
        with self.raw_layer():
            with self.assertRaises(AppError) as ctx:
                await service.build_timeline(self.repo, self.records, session=None,
                                             event_id="evt_1", node_scope="everything")
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_timeline_of_empty_event(self) -> None:
        empty = InMemoryEventRepository(events=[make_event("evt_empty")], links={})
        with self.raw_layer(contents={}):
            timeline = await service.build_timeline(empty, self.records, session=None,
                                                    event_id="evt_empty")

        self.assertEqual(timeline["nodes"], [])
        self.assertEqual(timeline["total_contents"], 0)

    async def test_clusters_sorted_by_size_with_representatives(self) -> None:
        with self.raw_layer():
            view = await service.list_clusters(self.repo, self.records, session=None,
                                              event_id="evt_1")

        self.assertEqual([c["label"] for c in view["items"]], ["大簇", "小簇"])
        big = view["items"][0]
        self.assertEqual(big["size"], 30)
        self.assertEqual(len(big["representatives"]), 1)
        self.assertEqual(big["representatives"][0]["content_uid"], WEIBO_KOL)
        self.assertIn("author", big["representatives"][0])        # 与动态流同构

    async def test_cluster_members_drilldown(self) -> None:
        with self.raw_layer():
            total, items = await service.list_cluster_members(
                self.repo, self.records, session=None, cluster_id="c1", page_size=1)

        self.assertEqual(total, 2)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["topic_name"], "星耀XPhone15")

    async def test_unknown_cluster_returns_404(self) -> None:
        with self.raw_layer():
            with self.assertRaises(AppError) as ctx:
                await service.list_cluster_members(self.repo, self.records, session=None,
                                                   cluster_id="c-none")
        self.assertEqual(ctx.exception.code, "BIZ_201")


class SourceTypeCoverageTest(unittest.TestCase):
    """四个信源的标签都要能出现在平台字段里（避免某个信源静默缺失）。"""

    def test_all_source_types_have_labels(self) -> None:
        for source_type in SourceType:
            self.assertTrue(source_type.label)


if __name__ == "__main__":
    unittest.main()
