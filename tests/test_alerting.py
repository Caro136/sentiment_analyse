"""预警引擎测试（FR-52~FR-56）：规则判定、生命周期、通知降级、监听器。

这一层的错误全是**静默**的：规则算错只会「该报的没报」，状态机写松只会让
「已关闭」的告警复活，通知降级不标记只会让「告警发了但没人收到」无人知晓。
因此测试的重点是把这些边界逐条钉死，而不是验证「能跑通」。
"""

import asyncio
import unittest
from unittest.mock import patch

from engines.alerting import lifecycle
from engines.alerting import service as alert_service
from engines.alerting.listener import AlertListener
from engines.alerting.notifiers import email_notifier
from engines.alerting.rules import (
    SURGE_MIN_NEGATIVE,
    RuleSpec,
    build_window_stats,
    evaluate_rules,
)
from engines.contracts.config import Settings
from engines.contracts.enums import AlertRuleType, AlertStatus, RiskLevel
from tests.fakes import make_content

SURGE_RULE = RuleSpec(rule_id="rule_surge", rule_type="negative_surge", level="A",
                      threshold=0.5)
SENSITIVE_RULE = RuleSpec(rule_id="rule_sensitive", rule_type="sensitive_hit", level="S",
                          threshold=2, sensitive_words=("退款", "爆炸"))
KOL_RULE = RuleSpec(rule_id="rule_kol", rule_type="kol_voice", level="B", threshold=1)


def entries(*specs):
    """构造 (内容, 情感, 风险) 列表。specs 每项为 (uid, sentiment, risk, kwargs)。"""
    rows = []
    for index, (sentiment, risk, kwargs) in enumerate(specs):
        uid = f"social_media:weibo_note:{index + 1}"
        rows.append((make_content(uid, **kwargs), sentiment, risk))
    return rows


class SurgeRuleTest(unittest.TestCase):
    def test_below_threshold_does_not_fire(self) -> None:
        current = entries(*[("negative", "C", {})] * 6)
        previous = entries(*[("negative", "C", {})] * 5)      # +20%，阈值 50%
        stats = build_window_stats(current, previous)

        self.assertEqual(evaluate_rules([SURGE_RULE], stats), [])

    def test_above_threshold_fires_with_ratio(self) -> None:
        current = entries(*[("negative", "A", {})] * 10)
        previous = entries(*[("negative", "C", {})] * 4)      # +150%
        stats = build_window_stats(current, previous)

        hits = evaluate_rules([SURGE_RULE], stats)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].rule.level, "A")
        self.assertAlmostEqual(hits[0].detail["surge_ratio"], 1.5, places=4)
        self.assertIn("150%", hits[0].reason)

    def test_small_absolute_change_does_not_fire(self) -> None:
        """1 条 → 3 条也是 +200%，但没有门槛的话它就成了 A 级告警。"""
        current = entries(*[("negative", "A", {})] * (SURGE_MIN_NEGATIVE - 2))
        previous = entries(*[("negative", "C", {})] * 1)
        stats = build_window_stats(current, previous)

        self.assertEqual(evaluate_rules([SURGE_RULE], stats), [])

    def test_zero_baseline_is_annotated(self) -> None:
        """上一窗口没有负向：不能算出「无穷大增幅」，要按基数 1 计并如实标注。"""
        current = entries(*[("negative", "A", {})] * 8)
        stats = build_window_stats(current, [])

        hits = evaluate_rules([SURGE_RULE], stats)
        self.assertEqual(len(hits), 1)
        self.assertIn("基数 1", hits[0].detail["note"])

    def test_neutral_and_positive_not_counted(self) -> None:
        current = entries(*[("negative", "A", {})] * 6 + [("neutral", "C", {})] * 20
                          + [("positive", "C", {})] * 20)
        previous = entries(*[("negative", "C", {})] * 3)
        stats = build_window_stats(current, previous)

        self.assertEqual(stats.negative_count, 6)
        self.assertEqual(stats.total_count, 46)

    def test_threshold_defaults_to_half_when_unset(self) -> None:
        rule = RuleSpec(rule_id="r", rule_type="negative_surge", level="A", threshold=None)
        current = entries(*[("negative", "C", {})] * 10)
        previous = entries(*[("negative", "C", {})] * 5)

        hits = evaluate_rules([rule], build_window_stats(current, previous))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].detail["threshold"], 0.5)


class SensitiveRuleTest(unittest.TestCase):
    def test_fires_when_hits_reach_threshold(self) -> None:
        current = entries(("negative", "S", {"text": "要求退款，不然就投诉"}),
                          ("negative", "A", {"text": "电池爆炸了"}),
                          ("neutral", "C", {"text": "无关内容"}))
        stats = build_window_stats(current, [], sensitive_words=SENSITIVE_RULE.sensitive_words)

        hits = evaluate_rules([SENSITIVE_RULE], stats)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].detail["hit_count"], 2)
        self.assertEqual(set(hits[0].detail["words"]), {"退款", "爆炸"})

    def test_below_threshold_does_not_fire(self) -> None:
        current = entries(("negative", "S", {"text": "要求退款"}))
        stats = build_window_stats(current, [], sensitive_words=SENSITIVE_RULE.sensitive_words)

        self.assertEqual(evaluate_rules([SENSITIVE_RULE], stats), [])

    def test_no_word_library_never_fires(self) -> None:
        """没配词库的敏感词规则永远不该命中（否则它就是个噪音源）。"""
        rule = RuleSpec(rule_id="r", rule_type="sensitive_hit", level="S", threshold=1,
                        sensitive_words=())
        current = entries(*[("negative", "S", {"text": "退款"})] * 5)
        stats = build_window_stats(current, [], sensitive_words=())

        self.assertEqual(evaluate_rules([rule], stats), [])

    def test_hit_samples_are_capped(self) -> None:
        current = entries(*[("negative", "S", {"text": "要求退款"})] * 30)
        stats = build_window_stats(current, [], sensitive_words=("退款",))

        self.assertLessEqual(len(stats.sensitive_hits), 10)


class KolRuleTest(unittest.TestCase):
    def test_high_influence_negative_fires(self) -> None:
        current = entries(("negative", "A", {"nickname": "数码实验室",
                                            "influence_score": 84.59,
                                            "verified_type": "黄V"}),
                          ("negative", "C", {"nickname": "路人", "influence_score": 12.0}))
        stats = build_window_stats(current, [])

        hits = evaluate_rules([KOL_RULE], stats)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].detail["hit_count"], 1)
        self.assertIn("数码实验室", hits[0].reason)

    def test_high_risk_positive_also_counts(self) -> None:
        """高权重账号的 A/S 级内容也要报——哪怕情感是正向（措辞激烈同样危险）。"""
        current = entries(("positive", "S", {"influence_score": 90.0}))
        stats = build_window_stats(current, [])

        self.assertEqual(len(evaluate_rules([KOL_RULE], stats)), 1)

    def test_low_influence_ignored(self) -> None:
        current = entries(*[("negative", "S", {"influence_score": 20.0})] * 5)
        stats = build_window_stats(current, [])

        self.assertEqual(evaluate_rules([KOL_RULE], stats), [])

    def test_ordinary_positive_ignored(self) -> None:
        """普通的正向内容不该被 KOL 规则捞进来。"""
        current = entries(("positive", "C", {"influence_score": 95.0}))
        stats = build_window_stats(current, [])

        self.assertEqual(evaluate_rules([KOL_RULE], stats), [])


class EvaluateRulesTest(unittest.TestCase):
    def test_unknown_rule_type_is_skipped_not_raised(self) -> None:
        """库里若有一条类型写错的规则，不该让整次扫描崩掉。"""
        bad = RuleSpec(rule_id="r", rule_type="typo_rule", level="A")
        current = entries(*[("negative", "A", {})] * 10)
        stats = build_window_stats(current, [])

        self.assertEqual(evaluate_rules([bad], stats), [])

    def test_all_rules_evaluated_independently(self) -> None:
        # 6 条：既过激增规则的最小条数门槛，又满足敏感词与 KOL 规则的阈值
        current = entries(*[("negative", "S", {"text": "要求退款",
                                              "influence_score": 90.0})] * 6)
        stats = build_window_stats(current, [], sensitive_words=("退款",))

        hits = evaluate_rules([SURGE_RULE, SENSITIVE_RULE, KOL_RULE], stats)
        self.assertEqual({hit.rule.rule_id for hit in hits},
                         {"rule_surge", "rule_sensitive", "rule_kol"})


class LifecycleTest(unittest.TestCase):
    def test_allowed_transitions(self) -> None:
        allowed = [
            ("unread", "read"), ("unread", "claimed"), ("unread", "closed"),
            ("read", "claimed"), ("read", "closed"),
            ("claimed", "closed"), ("claimed", "read"),
        ]
        for current, target in allowed:
            with self.subTest(current=current, target=target):
                self.assertTrue(lifecycle.can_transition(current, target))

    def test_closed_is_terminal(self) -> None:
        """已关闭的告警不能再被改回任何状态——否则告警列表的可信度就没了。"""
        for target in AlertStatus:
            with self.subTest(target=target.value):
                if target is AlertStatus.CLOSED:
                    continue
                self.assertFalse(lifecycle.can_transition("closed", target.value))

    def test_cannot_skip_backwards(self) -> None:
        self.assertFalse(lifecycle.can_transition("claimed", "unread"))
        self.assertFalse(lifecycle.can_transition("read", "unread"))

    def test_same_status_allowed(self) -> None:
        for status in AlertStatus:
            with self.subTest(status=status.value):
                self.assertTrue(lifecycle.can_transition(status.value, status.value))

    def test_unknown_status_rejected(self) -> None:
        self.assertFalse(lifecycle.can_transition("unread", "archived"))
        self.assertFalse(lifecycle.can_transition("archived", "read"))

    def test_transition_error_is_readable(self) -> None:
        message = lifecycle.transition_error("closed", "read")
        self.assertIn("已关闭", message)
        self.assertNotIn("已已", message)      # 状态标签自带「已」，别再叠一个

    def test_transition_error_lists_allowed_targets(self) -> None:
        """错误里要给出**可选项**，否则使用者只能去翻代码找合法状态。"""
        message = lifecycle.transition_error("claimed", "unread")
        allowed_part = message.split("可选：")[-1]
        self.assertEqual(set(allowed_part.split("/")), {"closed", "read"})


class NotifierTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = Settings(smtp_host="smtp.example.com", smtp_port=465,
                                 smtp_user="ops@example.com", smtp_password="secret-auth-code",
                                 alert_mail_to="oncall@example.com")

    async def test_unconfigured_smtp_is_skipped_not_failed(self) -> None:
        """没配 SMTP 不是失败：开发环境不该因为没邮箱就一路报错。"""
        result = await email_notifier.send_alert_mail(
            "主题", "正文", settings=Settings(smtp_host="", smtp_user=""))
        self.assertEqual(result.status, email_notifier.SEND_STATUS_SKIPPED)

    async def test_success_on_first_attempt(self) -> None:
        with patch.object(email_notifier, "_send_once") as send:
            result = await email_notifier.send_alert_mail("s", "b", settings=self.settings)

        self.assertEqual(result.status, email_notifier.SEND_STATUS_SENT)
        self.assertEqual(result.attempts, 1)
        send.assert_called_once()

    async def test_retries_then_degrades(self) -> None:
        """三次重试后降级为纯站内——**必须标记出来**，否则「发了但没收到」无人知晓。"""
        with patch.object(email_notifier, "_send_once",
                          side_effect=TimeoutError("连接超时")), \
                patch.object(email_notifier.asyncio, "sleep", new=_no_sleep):
            result = await email_notifier.send_alert_mail("s", "b", settings=self.settings)

        self.assertEqual(result.status, email_notifier.SEND_STATUS_DEGRADED)
        self.assertEqual(result.attempts, len(email_notifier.RETRY_DELAYS) + 1)
        self.assertIn("TimeoutError", result.error)

    async def test_error_message_is_masked(self) -> None:
        """异常文本可能带上授权码，落库前必须脱敏。"""
        with patch.object(
                email_notifier, "_send_once",
                side_effect=RuntimeError("login failed for password=secret-auth-code")), \
                patch.object(email_notifier.asyncio, "sleep", new=_no_sleep):
            result = await email_notifier.send_alert_mail("s", "b", settings=self.settings)

        self.assertNotIn("secret-auth-code", result.error or "")
        self.assertIn("***", result.error or "")

    async def test_recovers_on_second_attempt(self) -> None:
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("抖一下")

        with patch.object(email_notifier, "_send_once", side_effect=flaky), \
                patch.object(email_notifier.asyncio, "sleep", new=_no_sleep):
            result = await email_notifier.send_alert_mail("s", "b", settings=self.settings)

        self.assertEqual(result.status, email_notifier.SEND_STATUS_SENT)
        self.assertEqual(result.attempts, 2)


async def _no_sleep(_seconds: float) -> None:
    """把退避等待换成空转——否则测试要真等 65 秒。"""
    return None


class _FakeScope:
    """监听器里 `async with session_scope() as session` 的替身（不需要真会话）。"""

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc_info):
        return False


def _fake_scope():
    return _FakeScope()
    """把退避等待换成空转——否则测试要真等 65 秒。"""
    return None


class AlertMessageTest(unittest.TestCase):
    def test_message_contains_actionable_fields(self) -> None:
        from engines.alerting.repository import AlertRecordSnapshot

        snapshot = AlertRecordSnapshot(
            alert_id="alert_1", rule_id="rule_1", event_id="evt_星耀XPhone15", level="A",
            status="unread", triggered_at=1792044000, responded_at=None, merged_count=1,
            detail={}, notify_status=None, notify_error=None)
        subject, body = alert_service.build_alert_message(snapshot, {
            "rule_type": "negative_surge", "topic_id": "tp1",
            "reason": "负向声量环比激增 150%", "negative_count": 10,
            "prev_negative_count": 4, "surge_ratio": 1.5})

        self.assertIn("A级", subject)
        self.assertIn("tp1", subject)
        self.assertIn("负向声量环比激增 150%", body)
        self.assertIn("10 条", body)
        self.assertIn("/workbench", body)

    def test_sensitive_message_lists_words_and_samples(self) -> None:
        from engines.alerting.repository import AlertRecordSnapshot

        snapshot = AlertRecordSnapshot(
            alert_id="a", rule_id="r", event_id=None, level="S", status="unread",
            triggered_at=1792044000, responded_at=None, merged_count=1, detail={},
            notify_status=None, notify_error=None)
        _, body = alert_service.build_alert_message(snapshot, {
            "rule_type": "sensitive_hit", "words": ["退款", "爆炸"],
            "hits": [{"risk_level": "S", "text": "电池爆炸了"}]})

        self.assertIn("退款, 爆炸", body)
        self.assertIn("电池爆炸了", body)


class ListenerTest(unittest.IsolatedAsyncioTestCase):
    """回调 O(1)、积压折叠、失败不拖垮循环。"""

    async def test_enqueue_is_non_blocking_and_counts_drops(self) -> None:
        listener = AlertListener(queue_size=1)
        from engines.contracts.events import ContentLabeledEvent

        listener.enqueue(ContentLabeledEvent(content_uids=("a",)))
        listener.enqueue(ContentLabeledEvent(content_uids=("b",)))     # 队列满 → 丢弃

        self.assertEqual(listener._queue_full, 1)
        self.assertEqual(listener._queue.qsize(), 1)

    async def test_worker_folds_backlog_into_one_scan(self) -> None:
        """一次批量打标会连发多个事件，只应扫一次。"""
        from engines.contracts.events import ContentLabeledEvent

        listener = AlertListener()
        scans: list[int] = []

        async def fake_handle(event, drained):
            scans.append(drained)

        with patch.object(listener, "_handle", fake_handle):
            task = asyncio.create_task(listener._worker())
            for index in range(3):
                listener.enqueue(ContentLabeledEvent(content_uids=(f"c{index}",)))
            await asyncio.sleep(0.05)
            task.cancel()

        self.assertEqual(scans, [3])

    async def test_publish_reaches_scan_via_bus(self) -> None:
        """完整接缝：analytics 发布事件 → 总线 → 监听器 → 扫描。

        这条链路断了的表现是**预警静默失效**：内容照常入库、大盘照常更新，
        就是一条告警都不出来，且没有任何报错。
        """
        from engines.common.eventing.bus import get_event_bus, reset_event_bus
        from engines.common.eventing.publishers import publish_content_labeled
        from engines.contracts.events import EventType

        reset_event_bus()
        listener = AlertListener()
        received: list = []

        async def fake_scan_event(event, session):
            received.append(event)
            return None

        from engines.alerting import service as alert_service

        with patch.object(alert_service, "scan_event", fake_scan_event), \
                patch("engines.alerting.listener.session_scope", _fake_scope):
            listener.start()
            publish_content_labeled(["social_media:weibo_note:1"],
                                    topic_ids=["tp1"], negative_count=1)
            await asyncio.sleep(0.1)
            listener.stop()

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].topic_ids, ("tp1",))
        self.assertEqual(received[0].negative_count, 1)
        # 订阅已解除：再发不会有人处理
        self.assertEqual(get_event_bus().subscriber_count(EventType.CONTENT_LABELED), 0)

    async def test_worker_survives_handler_failure(self) -> None:
        from engines.contracts.events import ContentLabeledEvent

        listener = AlertListener()
        attempts = {"n": 0}

        async def boom(event, drained):
            attempts["n"] += 1
            raise RuntimeError("扫描炸了")

        with patch.object(listener, "_handle", boom):
            task = asyncio.create_task(listener._worker())
            listener.enqueue(ContentLabeledEvent(content_uids=("a",)))
            await asyncio.sleep(0.02)
            listener.enqueue(ContentLabeledEvent(content_uids=("b",)))
            await asyncio.sleep(0.05)
            task.cancel()

        self.assertEqual(attempts["n"], 2)      # 第一次失败没有终止循环


if __name__ == "__main__":
    unittest.main()
