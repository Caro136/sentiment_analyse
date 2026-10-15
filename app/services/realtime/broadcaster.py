"""事件广播：一写多读地扇出到 SSE 连接。

**回调必须 O(1)**：发布方（正在跑的研究员）是同步调用的，
回调里有任何阻塞都会拖慢它。所以回调只做 `put_nowait`，
慢消费者的问题由队列策略处理，不反馈给发布方。

**慢消费者会拖垮所有人**：一个卡住的浏览器连接会让它的队列无限增长，
把内存吃光。所以每个连接有容量上限，满了就**丢弃最旧的**消息——
SSE 是进度通知，丢一条不影响正确性，但卡住整个服务影响所有人。

**只转发三类事件**：进度、结果、错误。
章节就绪（SECTION_READY）是内部协同信号，前端不需要知道；
主持人讨论消息走轮询接口（它需要保留历史，SSE 是即时的，两者语义不同）。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from engines.common.eventing.bus import get_event_bus
from engines.common.logging import logger
from engines.contracts.events import BaseEvent, EventType

# 转发给前端的事件类型
FORWARDED_EVENT_TYPES = (
    EventType.ROLE_PROGRESS,
    EventType.ROLE_RESULT,
    EventType.ROLE_ERROR,
    EventType.SECTION_READY,          # 前端用它更新"已完成章节"列表
)

# 单个连接的队列容量。超过即丢弃最旧的消息
MAX_QUEUE_SIZE = 200


class Broadcaster:
    """事件总线的订阅者，把事件扇出到若干 SSE 连接。"""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._bus = get_event_bus()
        self._started = False
        self.dropped = 0          # 累计丢弃数，用于诊断

    # ------------------------------------------------------------------

    def start(self) -> None:
        """订阅事件总线。重复调用是幂等的。"""
        if self._started:
            return
        for event_type in FORWARDED_EVENT_TYPES:
            self._bus.subscribe(event_type, self._on_event)
        self._started = True
        logger.info("SSE 广播已启动，转发 {} 类事件", len(FORWARDED_EVENT_TYPES))

    def stop(self) -> None:
        """取消订阅并断开所有连接。"""
        if not self._started:
            return
        for event_type in FORWARDED_EVENT_TYPES:
            self._bus.unsubscribe(event_type, self._on_event)
        self._started = False
        self._subscribers.clear()
        logger.info("SSE 广播已停止")

    # ------------------------------------------------------------------

    def _on_event(self, event: BaseEvent) -> None:
        """事件回调。**只做 put_nowait，不做任何耗时操作。**"""
        if not self._subscribers:
            return
        payload = event.to_payload()
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                # 慢消费者：丢最旧的再放新的，保证它还能看到最新进度
                self.dropped += 1
                try:
                    queue.get_nowait()
                    queue.put_nowait(payload)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        """SSE 连接的生命周期：订阅 → 使用 → 自动清理。

        用上下文管理器而不是手动 subscribe/unsubscribe：
        客户端断线的方式有很多种（关标签页、网络断、超时），
        靠调用方记得清理必然会漏，队列就会越积越多。
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.add(queue)
        logger.info("SSE 连接建立（当前 {} 个）", len(self._subscribers))
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
            logger.info("SSE 连接断开（剩余 {} 个）", len(self._subscribers))

    # ------------------------------------------------------------------

    @property
    def connection_count(self) -> int:
        return len(self._subscribers)

    @property
    def is_running(self) -> bool:
        return self._started

    def stats(self) -> dict:
        return {
            "running": self._started,
            "connections": self.connection_count,
            "dropped": self.dropped,
            "forwarded_types": [t.value for t in FORWARDED_EVENT_TYPES],
        }


_broadcaster: Broadcaster | None = None


def get_broadcaster() -> Broadcaster:
    """进程内单例。**必须单 worker 运行**（设计规格 ADR-05）：
    多 worker 下事件只会到达持有订阅的那个进程，SSE 连接会随机收不到消息。
    """
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = Broadcaster()
    return _broadcaster


def reset_broadcaster() -> None:
    global _broadcaster
    if _broadcaster is not None:
        _broadcaster.stop()
    _broadcaster = None


def format_sse(payload: dict) -> str:
    """格式化为 SSE 数据帧。"""
    return json.dumps(payload, ensure_ascii=False)
