"""监测配置用例测试（FR-06~FR-13）。

重点覆盖三类「不报错但结果不对」的地方：

1. **主题关键词的推导与跟随**——它决定接入层能不能召回到内容，错了的表现是
   「主题建好了但没有任何数据」，很容易被当成数据覆盖问题；
2. **数据范围**——分析师改他人主题必须被拦（PRD 第 2 章），漏了就是越权；
3. **运行配置白名单**——类型转换、越界取值、敏感键，任一处松动都会静默生效。
"""

import unittest
from unittest.mock import patch

from app.errors import AppError
from app.services.auth import passwords
from app.services.monitoring import channels as channel_service
from app.services.monitoring import runtime_config as config_service
from app.services.monitoring import topics as topic_service
from app.services.monitoring.repository import KeywordRecord
from engines.contracts.config import Settings
from engines.contracts.enums import KeywordGroup, SourceType
from engines.ingestion.reader import SourceProbe
from tests.fakes import (
    InMemoryMonitoringRepository,
    capture_audit_writes,
    capture_probe,
    make_topic,
    make_user,
)


class KeywordItem:
    """轻量替身：只需 word / group_type / weight 三个属性。"""

    def __init__(self, word: str, group_type: str, weight: float = 1.0) -> None:
        self.word = word
        self.group_type = group_type
        self.weight = weight


def brand(word: str, weight: float = 1.0) -> KeywordItem:
    return KeywordItem(word, KeywordGroup.BRAND.value, weight)


def event(word: str, weight: float = 1.0) -> KeywordItem:
    return KeywordItem(word, KeywordGroup.EVENT.value, weight)


def exclude(word: str, weight: float = 1.0) -> KeywordItem:
    return KeywordItem(word, KeywordGroup.EXCLUDE.value, weight)


class AuditCaptureTestCase(unittest.IsolatedAsyncioTestCase):
    """所有被审计的服务调用都要拦下审计写入，否则测试会依赖本机数据库。"""

    async def asyncSetUp(self) -> None:
        # 口令哈希按生产参数算是刻意的慢（约 40ms/次），而每个用例都要造几个用户
        # 来当操作人；测试期降到最低档，避免整套测试被哈希拖垮
        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.audit_rows: list = []
        patcher = capture_audit_writes(self.audit_rows)
        patcher.start()
        self.addCleanup(patcher.stop)


class KeywordNormalizeTest(unittest.TestCase):
    def test_same_group_duplicate_is_merged_keeping_max_weight(self) -> None:
        records = topic_service.normalize_keywords(
            [brand("星耀", 0.5), brand("星耀", 2.0)])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].weight, 2.0)

    def test_same_word_in_two_recall_groups_kept(self) -> None:
        """品牌词与竞品词撞题是合法的——监测词与竞品词可能同名。"""
        records = topic_service.normalize_keywords([brand("星耀"), event("星耀")])
        self.assertEqual(len(records), 2)

    def test_word_in_recall_and_exclude_group_rejected(self) -> None:
        """召回它又排除它，命中与否取决于匹配顺序——必须显式失败。"""
        with self.assertRaises(AppError) as ctx:
            topic_service.normalize_keywords([brand("星耀"), exclude("星耀")])

        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("星耀", ctx.exception.message)
        self.assertIn("排除词", ctx.exception.message)

    def test_blank_word_rejected(self) -> None:
        with self.assertRaises(AppError):
            topic_service.normalize_keywords([brand("   ")])

    def test_words_are_trimmed(self) -> None:
        records = topic_service.normalize_keywords([brand("  星耀  ")])
        self.assertEqual(records[0].word, "星耀")


class SourceKeywordTest(unittest.TestCase):
    def test_brand_wins_over_event_and_competitor(self) -> None:
        records = [event("购置税减免"), brand("星耀XPhone15"), brand("星耀", 2.0)]
        self.assertEqual(topic_service.derive_source_keyword(records), "星耀")

    def test_highest_weight_within_group(self) -> None:
        records = [brand("星耀", 0.5), brand("星耀科技", 1.5)]
        self.assertEqual(topic_service.derive_source_keyword(records), "星耀科技")

    def test_exclude_words_never_chosen(self) -> None:
        """排除词权重再高也不能当主题关键词——它的作用是收窄，不是召回。"""
        records = [KeywordRecord("噪声品牌", KeywordGroup.EXCLUDE.value, 9.0)]
        self.assertIsNone(topic_service.derive_source_keyword(records))

    def test_no_recall_words_returns_none(self) -> None:
        self.assertIsNone(topic_service.derive_source_keyword([]))


class TopicServiceTest(AuditCaptureTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.repo = InMemoryMonitoringRepository()
        self.analyst = make_user("analyst", role="analyst")
        self.other_analyst = make_user("analyst2", role="analyst", user_id="u-analyst2")
        self.admin = make_user("admin", role="admin")

    async def test_create_derives_source_keyword_from_keywords(self) -> None:
        bundle = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="星耀XPhone15",
            keywords=[brand("星耀"), event("散热"), exclude("二手")])

        self.assertEqual(bundle.topic.source_keyword, "星耀")
        self.assertEqual(bundle.topic.owner_id, self.analyst.user_id)
        self.assertEqual(bundle.topic.status, "enabled")
        self.assertEqual(len(bundle.keywords), 3)

    async def test_create_uses_explicit_source_keyword(self) -> None:
        bundle = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="政策",
            source_keyword="新能源车购置税", keywords=[brand("购置税")])
        self.assertEqual(bundle.topic.source_keyword, "新能源车购置税")

    async def test_create_without_recall_words_logs_and_allows(self) -> None:
        """先建主题后补词包是允许的；此时没有主题关键词，接入时召不到内容。"""
        bundle = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="空主题", keywords=[exclude("噪声")])
        self.assertIsNone(bundle.topic.source_keyword)

    async def test_analyst_cannot_assign_owner(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await topic_service.create_topic(
                self.repo, actor=self.analyst, name="越权", owner_id=self.other_analyst.user_id)
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_admin_can_assign_owner(self) -> None:
        bundle = await topic_service.create_topic(
            self.repo, actor=self.admin, name="指派", owner_id=self.analyst.user_id)
        self.assertEqual(bundle.topic.owner_id, self.analyst.user_id)

    async def test_unknown_channel_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await topic_service.create_topic(
                self.repo, actor=self.analyst, name="通道写错",
                keywords=[brand("星耀")], channel_ids=["ch_not_exist"])
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_analyst_cannot_update_others_topic(self) -> None:
        created = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="我的主题", keywords=[brand("星耀")])

        with self.assertRaises(AppError) as ctx:
            await topic_service.update_topic(
                self.repo, actor=self.other_analyst,
                topic_id=created.topic.topic_id, name="改名")

        self.assertEqual(ctx.exception.code, "AUTH_003")

    async def test_admin_can_update_others_topic(self) -> None:
        created = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="分析师的主题", keywords=[brand("星耀")])

        updated = await topic_service.update_topic(
            self.repo, actor=self.admin, topic_id=created.topic.topic_id,
            name="管理员改名", status="disabled")

        self.assertEqual(updated.topic.name, "管理员改名")
        self.assertEqual(updated.topic.status, "disabled")

    async def test_update_records_before_and_after(self) -> None:
        created = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="原名称", keywords=[brand("星耀")])
        self.audit_rows.clear()

        await topic_service.update_topic(
            self.repo, actor=self.analyst, topic_id=created.topic.topic_id, name="新名称")

        import json
        row = self.audit_rows[-1]
        self.assertEqual(row.action, "config_update")
        self.assertEqual(json.loads(row.before_value)["name"], "原名称")
        self.assertEqual(json.loads(row.after_value)["name"], "新名称")

    async def test_missing_topic_returns_404(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await topic_service.get_topic(self.repo, "tp-not-exist")
        self.assertEqual(ctx.exception.code, "BIZ_201")

    async def test_replace_keywords_follows_derived_source_keyword(self) -> None:
        """主题关键词来自推导时随词包更新——否则改完词包还按旧词召回。"""
        created = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="跟随", keywords=[brand("星耀")])
        self.assertEqual(created.topic.source_keyword, "星耀")

        bundle = await topic_service.replace_keywords(
            self.repo, actor=self.analyst, topic_id=created.topic.topic_id,
            keywords=[brand("星耀科技")])

        self.assertEqual(bundle.topic.source_keyword, "星耀科技")

    async def test_replace_keywords_keeps_explicit_source_keyword(self) -> None:
        """显式写死的主题关键词不该被词包改动覆盖——那是有意为之的取值。"""
        created = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="手填",
            source_keyword="新能源车购置税", keywords=[brand("购置税")])

        bundle = await topic_service.replace_keywords(
            self.repo, actor=self.analyst, topic_id=created.topic.topic_id,
            keywords=[brand("新能源车")])

        self.assertEqual(bundle.topic.source_keyword, "新能源车购置税")

    async def test_list_returns_group_stats(self) -> None:
        await topic_service.create_topic(
            self.repo, actor=self.analyst, name="统计",
            keywords=[brand("星耀"), brand("星耀科技"), event("散热")])

        total, bundles = await topic_service.list_topics(self.repo)
        self.assertEqual(total, 1)
        self.assertEqual(len(bundles[0].keywords), 3)

    async def test_replace_channels_links_known_channels(self) -> None:
        await channel_service.ensure_default_channels(self.repo)
        created = await topic_service.create_topic(
            self.repo, actor=self.analyst, name="关联通道", keywords=[brand("星耀")])

        bundle = await topic_service.replace_channels(
            self.repo, actor=self.analyst, topic_id=created.topic.topic_id,
            channel_ids=["ch_social_media", "ch_short_video"])

        self.assertEqual(bundle.channel_ids, ["ch_social_media", "ch_short_video"])


class ChannelServiceTest(AuditCaptureTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.repo = InMemoryMonitoringRepository()

    async def test_ensure_default_channels_creates_four_in_fixed_order(self) -> None:
        channels = await channel_service.ensure_default_channels(self.repo)

        self.assertEqual([c.channel_type for c in channels],
                         [t.value for t in SourceType])

    async def test_ensure_is_idempotent_and_keeps_user_changes(self) -> None:
        await channel_service.ensure_default_channels(self.repo)
        await channel_service.update_channel(self.repo, channel_id="ch_short_video",
                                             enabled=False, poll_interval_min=60)

        channels = await channel_service.ensure_default_channels(self.repo)
        video = next(c for c in channels if c.channel_id == "ch_short_video")

        self.assertFalse(video.enabled)              # 用户改过的不被默认值覆盖
        self.assertEqual(video.poll_interval_min, 60)
        self.assertEqual(len(channels), 4)           # 也不会重复创建

    async def test_disable_keeps_history_untouched(self) -> None:
        """FR-11 的验收要求：停用只影响后续采集，历史数据照常可查。"""
        await channel_service.ensure_default_channels(self.repo)
        updated = await channel_service.update_channel(
            self.repo, channel_id="ch_news_portal", enabled=False)

        self.assertFalse(updated.enabled)
        self.assertEqual(updated.lookback_hours, 72)          # 其余参数不动
        self.assertIsNotNone(self.repo.channels["ch_news_portal"])

    async def test_update_records_before_and_after(self) -> None:
        await channel_service.ensure_default_channels(self.repo)
        self.audit_rows.clear()

        await channel_service.update_channel(self.repo, channel_id="ch_social_media",
                                             poll_interval_min=30)

        import json
        row = self.audit_rows[-1]
        self.assertEqual(row.target, "channel:ch_social_media")
        self.assertEqual(json.loads(row.before_value)["poll_interval_min"], 15)
        self.assertEqual(json.loads(row.after_value)["poll_interval_min"], 30)

    async def test_unknown_channel_returns_404(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await channel_service.update_channel(self.repo, channel_id="ch_nope",
                                                 enabled=False)
        self.assertEqual(ctx.exception.code, "BIZ_201")

    async def test_check_reports_probe_facts(self) -> None:
        await channel_service.ensure_default_channels(self.repo)
        probe = SourceProbe(source_type=SourceType.SOCIAL, content_table="weibo_note",
                            comment_table="weibo_note_comment", content_rows=123,
                            comment_rows=456, latest_published_at=1792000000)

        with capture_probe(probe):
            results = await channel_service.check_channels(self.repo, session=None)

        social = next(r for r in results if r["channel_id"] == "ch_social_media")
        self.assertTrue(social["db_reachable"])
        self.assertEqual(social["content_rows"], 123)
        self.assertEqual(social["latest_published_at"], 1792000000)
        self.assertIn("123", social["message"])

    async def test_check_degrades_when_probe_fails(self) -> None:
        """自检自身不能把接口打挂：探测失败要如实报告，而不是抛出去。"""
        await channel_service.ensure_default_channels(self.repo)

        with capture_probe(error=RuntimeError("连接被拒绝")):
            results = await channel_service.check_channels(self.repo, session=None)

        self.assertEqual(len(results), 4)
        for item in results:
            self.assertFalse(item["db_reachable"])
            self.assertIn("RuntimeError", item["message"])

    async def test_check_reports_disabled_channel(self) -> None:
        await channel_service.ensure_default_channels(self.repo)
        await channel_service.update_channel(self.repo, channel_id="ch_industry_forum",
                                             enabled=False)
        probe = SourceProbe(source_type=SourceType.FORUM, content_table="forum_thread",
                            comment_table="forum_post", content_rows=0, comment_rows=0,
                            latest_published_at=None)

        with capture_probe(probe):
            results = await channel_service.check_channels(self.repo, session=None,
                                                          channel_id="ch_industry_forum")

        self.assertEqual(len(results), 1)
        self.assertIn("已停用", results[0]["message"])


class RuntimeConfigTest(AuditCaptureTestCase):
    """运行配置白名单（FR-53 / 设计规格 8.1）。"""

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        config_service.reset_runtime_overrides()
        self.settings = Settings(session_secret="test-secret")

    async def test_whitelist_contains_no_secret_like_keys(self) -> None:
        """白名单里出现 KEY/SECRET/PASSWORD 一类键名就该失败。

        一个能改密钥的接口等于把密钥管理下放给任意管理员账号，
        而它毫无必要——所以这条是硬约束，不是提醒。
        """
        import re

        pattern = re.compile(config_service.SENSITIVE_KEY_PATTERN, re.IGNORECASE)
        offenders = [key for key in config_service.CONFIG_KEYS if pattern.search(key)]
        self.assertEqual(offenders, [])

    async def test_read_returns_whitelisted_values(self) -> None:
        entries = config_service.read_config(self.settings)
        keys = {e.key for e in entries}

        self.assertEqual(keys, set(config_service.CONFIG_KEYS))
        self.assertIn("INTENSITY_W_NEGATIVE", keys)
        self.assertNotIn("SESSION_SECRET", keys)
        self.assertFalse(any(e.overridden for e in entries))

    async def test_apply_converts_types_and_marks_overridden(self) -> None:
        entries = await config_service.apply_updates(
            {"INTENSITY_W_VOLUME": "0.45", "INSIGHT_VECTOR_TOP_K": "120"},
            settings=self.settings)

        by_key = {e.key: e for e in entries}
        self.assertEqual(self.settings.intensity_w_volume, 0.45)
        self.assertEqual(self.settings.insight_vector_top_k, 120)
        self.assertIsInstance(self.settings.intensity_w_volume, float)
        self.assertTrue(by_key["INTENSITY_W_VOLUME"].overridden)

    async def test_uppercase_key_actually_changes_the_value(self) -> None:
        """大写键名必须被转成字段名，否则 pydantic 会静默丢弃整条更新。

        实测踩过：`Settings(**{"INTENSITY_W_VOLUME": 0.45})` 因为大写键不是字段名、
        且配置是 `extra="ignore"`，构造出的实例值原封不动——接口返回「已更新」，
        配置却一点没变。这条测试就是防它回潮。
        """
        await config_service.apply_updates({"INSIGHT_VECTOR_TOP_K": 200},
                                          settings=self.settings)
        self.assertEqual(self.settings.insight_vector_top_k, 200)

    async def test_unknown_key_rejects_whole_batch(self) -> None:
        """整批拒绝：部分成功会让管理员以为改动都生效了。"""
        with self.assertRaises(AppError) as ctx:
            await config_service.apply_updates(
                {"INTENSITY_W_VOLUME": 0.45, "SESSION_SECRET": "hijacked"},
                settings=self.settings)

        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("SESSION_SECRET", ctx.exception.message)
        self.assertEqual(self.settings.intensity_w_volume, 0.30)   # 合法项也没生效

    async def test_out_of_range_value_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await config_service.apply_updates({"INTENSITY_W_VOLUME": 1.5},
                                              settings=self.settings)
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_illegal_type_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await config_service.apply_updates({"INSIGHT_VECTOR_TOP_K": "很多"},
                                              settings=self.settings)
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_illegal_enum_value_rejected(self) -> None:
        """供应商名写错不会报错、只会让公域检索全部降级为空，因此必须显式校验。"""
        with self.assertRaises(AppError) as ctx:
            await config_service.apply_updates({"SEARCH_TOOL_TYPE": "googl"},
                                              settings=self.settings)
        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("bocha", str(ctx.exception.detail))

    async def test_empty_updates_rejected(self) -> None:
        with self.assertRaises(AppError):
            await config_service.apply_updates({}, settings=self.settings)

    async def test_log_level_update_reinitialises_logging(self) -> None:
        with patch("engines.common.logging.setup_logging") as setup:
            await config_service.apply_updates({"LOG_LEVEL": "WARNING"},
                                              settings=self.settings)

        setup.assert_called_once_with("WARNING")

    async def test_audit_records_before_and_after(self) -> None:
        import json

        await config_service.apply_updates({"INTENSITY_W_VOLUME": 0.45},
                                          settings=self.settings)

        row = self.audit_rows[-1]
        self.assertEqual(row.action, "config_update")
        self.assertEqual(row.target, "config")
        self.assertEqual(json.loads(row.before_value)["INTENSITY_W_VOLUME"], 0.3)
        self.assertEqual(json.loads(row.after_value)["INTENSITY_W_VOLUME"], 0.45)


if __name__ == "__main__":
    unittest.main()
