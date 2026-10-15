"""SSE 广播测试。

重点覆盖三件事：扇出是否到达所有连接、慢消费者会不会拖垮别人、
连接断开后队列有没有被清理（漏清理会让内存随连接数无限增长）。
"""

import asyncio
import json
import unittest

from app.services.realtime.broadcaster import (
    FORWARDED_EVENT_TYPES,
    MAX_QUEUE_SIZE,
    Broadcaster,
    format_sse,
)
from engines.common.eventing.bus import reset_event_bus
from engines.contracts.events import (
    EventType,
    HostDiscussionMessageEvent,
    RoleProgressEvent,
    SectionReadyEvent,
)


class BroadcasterTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_event_bus()          # 每个用例用干净的总线
        self.broadcaster = Broadcaster()
        self.broadcaster.start()

    def tearDown(self) -> None:
        self.broadcaster.stop()
        reset_event_bus()

    def _progress(self, role: str = "insight") -> RoleProgressEvent:
        return RoleProgressEvent(role=role, status="planning", progress_pct=15)

    # ------------------------------------------------------------------

    def test_forwards_to_all_connections(self) -> None:
        """一写多读：一个事件要到达所有订阅者。"""
        async def run():
            async with self.broadcaster.subscribe() as first, \
                    self.broadcaster.subscribe() as second:
                from engines.common.eventing.bus import get_event_bus
                get_event_bus().publish(self._progress())
                await asyncio.sleep(0)
                return first.qsize(), second.qsize()

        first_size, second_size = asyncio.run(run())
        self.assertEqual(first_size, 1)
        self.assertEqual(second_size, 1)

    def test_no_subscribers_is_safe(self) -> None:
        """没有连接时发布事件不该出问题（发布方不关心有没有人听）。"""
        from engines.common.eventing.bus import get_event_bus
        get_event_bus().publish(self._progress())    # 不应抛异常

    def test_payload_carries_event_type(self) -> None:
        async def run():
            async with self.broadcaster.subscribe() as queue:
                from engines.common.eventing.bus import get_event_bus
                get_event_bus().publish(self._progress("media"))
                await asyncio.sleep(0)
                return await queue.get()

        payload = asyncio.run(run())
        self.assertEqual(payload["event_type"], "role_progress")
        self.assertEqual(payload["role"], "media")

    def test_section_ready_is_forwarded(self) -> None:
        """章节就绪也转发——前端用它更新"已完成章节"列表。"""
        async def run():
            async with self.broadcaster.subscribe() as queue:
                from engines.common.eventing.bus import get_event_bus
                get_event_bus().publish(SectionReadyEvent(
                    source="insight", section_key="heat_and_spread",
                    query="主题", body="正文"))
                await asyncio.sleep(0)
                return await queue.get()

        payload = asyncio.run(run())
        self.assertEqual(payload["event_type"], "section_ready")
        self.assertEqual(payload["section_key"], "heat_and_spread")

    def test_host_discussion_not_forwarded(self) -> None:
        """主持人讨论走轮询接口（需要保留历史），不经 SSE。"""
        event_type_names = {t.value for t in FORWARDED_EVENT_TYPES}
        self.assertNotIn(
            HostDiscussionMessageEvent.event_type.value, event_type_names)

    def test_slow_consumer_drops_oldest(self) -> None:
        """慢消费者丢最旧的消息，而不是无限增长把内存吃光。"""
        async def run():
            async with self.broadcaster.subscribe() as queue:
                from engines.common.eventing.bus import get_event_bus
                bus = get_event_bus()
                for index in range(MAX_QUEUE_SIZE + 20):
                    bus.publish(RoleProgressEvent(
                        role="insight", status="planning", progress_pct=index % 100))
                await asyncio.sleep(0)
                return queue.qsize(), self.broadcaster.dropped

        size, dropped = asyncio.run(run())
        self.assertEqual(size, MAX_QUEUE_SIZE)      # 不会超过上限
        self.assertGreater(dropped, 0)              # 确实丢了

    def test_one_slow_consumer_does_not_block_others(self) -> None:
        """一个卡住的连接不该影响其他连接拿到**最新**消息。

        两个队列都有容量上限，所以比"谁收得多"没有意义；
        有意义的是：被慢连接拖累的那个，最后收到的是不是最新一条。
        """
        total = MAX_QUEUE_SIZE + 5

        async def run():
            async with self.broadcaster.subscribe() as slow, \
                    self.broadcaster.subscribe() as healthy:
                from engines.common.eventing.bus import get_event_bus
                bus = get_event_bus()
                for index in range(total):
                    bus.publish(RoleProgressEvent(
                        role="insight", status="planning", progress_pct=index % 100))
                await asyncio.sleep(0)

                latest = None
                while not healthy.empty():
                    latest = healthy.get_nowait()
                return slow.qsize(), latest

        slow_size, latest = asyncio.run(run())
        self.assertEqual(slow_size, MAX_QUEUE_SIZE)
        self.assertIsNotNone(latest)
        # 最新发布的那条（progress_pct = (total-1) % 100）
        self.assertEqual(latest["progress_pct"], (total - 1) % 100)

    def test_subscription_cleaned_up_on_exit(self) -> None:
        """连接断开后队列必须被移除——漏清理会让内存随连接数增长。"""
        async def run():
            before = self.broadcaster.connection_count
            async with self.broadcaster.subscribe():
                during = self.broadcaster.connection_count
            after = self.broadcaster.connection_count
            return before, during, after

        before, during, after = asyncio.run(run())
        self.assertEqual(before, 0)
        self.assertEqual(during, 1)
        self.assertEqual(after, 0)

    def test_cleaned_up_even_on_exception(self) -> None:
        """连接内部抛异常时也要清理。"""
        async def run():
            try:
                async with self.broadcaster.subscribe():
                    raise RuntimeError("连接内部出错")
            except RuntimeError:
                pass
            return self.broadcaster.connection_count

        self.assertEqual(asyncio.run(run()), 0)

    def test_stop_unsubscribes(self) -> None:
        """停止后不再接收事件——否则进程重启会累积订阅者。"""
        async def run():
            async with self.broadcaster.subscribe() as queue:
                self.broadcaster.stop()
                from engines.common.eventing.bus import get_event_bus
                get_event_bus().publish(self._progress())
                await asyncio.sleep(0)
                return queue.qsize()

        self.assertEqual(asyncio.run(run()), 0)

    def test_start_is_idempotent(self) -> None:
        self.broadcaster.start()
        self.broadcaster.start()
        self.assertTrue(self.broadcaster.is_running)

    def test_stats(self) -> None:
        stats = self.broadcaster.stats()
        self.assertTrue(stats["running"])
        self.assertEqual(stats["connections"], 0)
        self.assertIn("role_progress", stats["forwarded_types"])


class FormatSseTest(unittest.TestCase):
    def test_json_keeps_chinese_readable(self) -> None:
        """中文不能被转义成 \\uXXXX——前端日志里会变成天书，排查困难。"""
        text = format_sse({"message": "正在生成章节"})
        self.assertIn("正在生成章节", text)
        self.assertNotIn("\\u", text)

    def test_round_trip(self) -> None:
        payload = {"event_type": "role_progress", "role": "insight", "progress_pct": 15}
        self.assertEqual(json.loads(format_sse(payload)), payload)


if __name__ == "__main__":
    unittest.main()
