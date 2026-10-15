"""事件总线与进度回调的单元测试。

重点覆盖两处**容易在真实链路上出错、但平时不会暴露**的行为：
  1. 订阅者内部抛异常时，其他订阅者是否照常收到事件；
  2. 回调中退订订阅者时，是否触发 "Set changed size during iteration"。
这两条在参考实现里都是踩过的坑，且只会在特定时序下偶发。
"""

import unittest

from engines.common.eventing.bus import EventBus, get_event_bus, reset_event_bus
from engines.common.eventing.publishers import (
    publish_host_message,
    publish_role_error,
    publish_role_progress,
    publish_section_ready,
)
from engines.common.progress import ProgressUpdate, report, stage_percent
from engines.contracts.events import (
    EventType,
    HostDiscussionMessageEvent,
    RoleProgressEvent,
    SectionReadyEvent,
)


class EventBusTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bus = EventBus()
        self.received: list = []

    def _collector(self):
        return lambda event: self.received.append(event)

    def test_subscribe_and_publish(self) -> None:
        self.bus.subscribe(EventType.ROLE_PROGRESS, self._collector())
        delivered = self.bus.publish(
            RoleProgressEvent(role="insight", status="planning", progress_pct=15))
        self.assertEqual(delivered, 1)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0].role, "insight")
        self.assertEqual(self.received[0].progress_pct, 15)

    def test_multiple_subscribers_all_receive(self) -> None:
        for _ in range(3):
            self.bus.subscribe(EventType.ROLE_PROGRESS, self._collector())
        delivered = self.bus.publish(RoleProgressEvent(role="media", status="searching"))
        self.assertEqual(delivered, 3)
        self.assertEqual(len(self.received), 3)

    def test_event_types_are_isolated(self) -> None:
        """订阅 ROLE_ERROR 不该收到 ROLE_PROGRESS。"""
        self.bus.subscribe(EventType.ROLE_ERROR, self._collector())
        self.bus.publish(RoleProgressEvent(role="insight", status="planning"))
        self.assertEqual(self.received, [])

    def test_duplicate_subscribe_is_idempotent(self) -> None:
        cb = self._collector()
        self.bus.subscribe(EventType.ROLE_PROGRESS, cb)
        self.bus.subscribe(EventType.ROLE_PROGRESS, cb)
        self.assertEqual(self.bus.publish(RoleProgressEvent(role="x", status="y")), 1)

    def test_unsubscribe(self) -> None:
        cb = self._collector()
        self.bus.subscribe(EventType.ROLE_PROGRESS, cb)
        self.bus.unsubscribe(EventType.ROLE_PROGRESS, cb)
        self.assertEqual(self.bus.publish(RoleProgressEvent(role="x", status="y")), 0)
        # 对未订阅的回调退订不应抛异常
        self.bus.unsubscribe(EventType.ROLE_PROGRESS, cb)

    def test_failing_subscriber_does_not_block_others(self) -> None:
        """一个订阅者崩溃不能影响其他订阅者。"""
        def broken(event):
            raise RuntimeError("订阅者内部错误")

        self.bus.subscribe(EventType.ROLE_PROGRESS, broken)
        self.bus.subscribe(EventType.ROLE_PROGRESS, self._collector())

        delivered = self.bus.publish(RoleProgressEvent(role="insight", status="planning"))
        self.assertEqual(delivered, 1)          # 正常的那个仍然收到
        self.assertEqual(len(self.received), 1)

    def test_unsubscribe_during_publish_is_safe(self) -> None:
        """回调中退订订阅者不应抛 Set changed size during iteration。"""
        holder = {}

        def first(event):
            self.bus.unsubscribe(EventType.ROLE_PROGRESS, holder["first"])
            self.received.append("first")

        holder["first"] = first
        self.bus.subscribe(EventType.ROLE_PROGRESS, first)
        self.bus.subscribe(EventType.ROLE_PROGRESS, self._collector())

        self.bus.publish(RoleProgressEvent(role="i", status="s"))   # 不应抛异常
        self.assertEqual(self.bus.subscriber_count(EventType.ROLE_PROGRESS), 1)

    def test_stats(self) -> None:
        self.bus.subscribe(EventType.ROLE_PROGRESS, self._collector())
        self.bus.publish(RoleProgressEvent(role="i", status="s"))
        stats = self.bus.stats()
        self.assertEqual(stats["role_progress"]["subscribers"], 1)
        self.assertEqual(stats["role_progress"]["published"], 1)


class EventModelTest(unittest.TestCase):
    def test_event_is_immutable(self) -> None:
        event = RoleProgressEvent(role="insight", status="planning")
        with self.assertRaises(Exception):
            event.role = "media"        # frozen 模型禁止赋值

    def test_payload_contains_event_type(self) -> None:
        payload = RoleProgressEvent(
            role="insight", status="planning", progress_pct=15).to_payload()
        self.assertEqual(payload["event_type"], "role_progress")
        self.assertEqual(payload["role"], "insight")
        self.assertIn("occurred_at", payload)

    def test_progress_pct_range_validated(self) -> None:
        with self.assertRaises(Exception):
            RoleProgressEvent(role="i", status="s", progress_pct=200)

    def test_section_ready_carries_query_for_topic_isolation(self) -> None:
        """query 是跨主题隔离的依据：没有它，上一个主题的章节会与本主题错误配对。"""
        event = SectionReadyEvent(
            source="insight", section_key="heat_and_spread",
            query="星耀XPhone15", body="正文", section_metadata={"hit_count": 12})
        self.assertEqual(event.query, "星耀XPhone15")
        self.assertEqual(event.section_metadata["hit_count"], 12)

    def test_host_message_final_flag(self) -> None:
        event = HostDiscussionMessageEvent(message_text="最终结论", is_final=True)
        self.assertIsNone(event.section_key)
        self.assertTrue(event.is_final)


class PublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_event_bus()
        self.received: list = []

    def test_progress_publisher(self) -> None:
        get_event_bus().subscribe(EventType.ROLE_PROGRESS, self.received.append)
        publish_role_progress("insight", "planning", "正在规划分析维度", 15)
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0].message, "正在规划分析维度")

    def test_error_publisher_masks_secret(self) -> None:
        """错误文本会推送到前端并落日志，必须先把密钥抹掉。"""
        get_event_bus().subscribe(EventType.ROLE_ERROR, self.received.append)
        publish_role_error("insight", "调用失败 Authorization: Bearer sk-abcdefghijklmn")
        text = self.received[0].error
        self.assertNotIn("abcdefghijklmn", text)
        self.assertIn("***", text)

    def test_section_ready_publisher(self) -> None:
        get_event_bus().subscribe(EventType.SECTION_READY, self.received.append)
        publish_section_ready(
            source="media", section_key="sentiment_and_opinion", query="q",
            body="b", agent_name="公域研究员", section_index=2, title="公众情感与观点")
        event = self.received[0]
        self.assertEqual(event.source, "media")
        self.assertEqual(event.section_index, 2)

    def test_host_message_publisher(self) -> None:
        get_event_bus().subscribe(EventType.HOST_DISCUSSION_MESSAGE, self.received.append)
        publish_host_message("逐维研判结论", section_key="heat_and_spread",
                             section_title="舆情热度与传播", query="q")
        self.assertEqual(self.received[0].section_key, "heat_and_spread")


class ProgressTest(unittest.TestCase):
    def test_stage_percent_mapping(self) -> None:
        self.assertEqual(stage_percent("planning", 0.0), 10)
        self.assertEqual(stage_percent("planning", 1.0), 20)
        self.assertEqual(stage_percent("summary", 0.5), 55)
        self.assertEqual(stage_percent("completed", 1.0), 100)

    def test_stage_percent_clamps_ratio(self) -> None:
        self.assertEqual(stage_percent("planning", 5.0), 20)
        self.assertEqual(stage_percent("planning", -1.0), 10)

    def test_report_with_none_callback_is_noop(self) -> None:
        report(None, "planning", "x")       # 不应抛异常

    def test_report_invokes_callback(self) -> None:
        got: list = []
        report(got.append, "searching", "检索中", ratio=1.0)
        self.assertEqual(got[0].status, "searching")
        self.assertEqual(got[0].progress_pct, 40)

    def test_report_accepts_explicit_percent(self) -> None:
        got: list = []
        report(got.append, "planning", "x", percent=18)
        self.assertEqual(got[0].progress_pct, 18)

    def test_progress_pct_is_clamped(self) -> None:
        self.assertEqual(ProgressUpdate(status="x", progress_pct=150).progress_pct, 100)
        self.assertEqual(ProgressUpdate(status="x", progress_pct=-5).progress_pct, 0)


if __name__ == "__main__":
    unittest.main()
