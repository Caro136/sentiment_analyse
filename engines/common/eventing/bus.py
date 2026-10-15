"""进程内事件总线。

设计取舍（设计规格 ADR-05）：不引入消息队列。事件在这里是**运行时可观测性**用途
（进度、讨论消息），不是业务事实的可靠传递——丢一条进度事件不影响正确性。
换来的是零运维组件与零序列化开销。

⚠️ **因此必须单 worker 运行**：多 worker 下事件只会到达持有订阅者的那个进程，
表现为「进度条卡住」「研判永不触发」。若将来需要多副本，把本模块换成 MQ 客户端即可，
调用方接口不变。

**回调约束**：回调必须是同步函数，且只做 O(1) 操作（典型做法是 `queue.put_nowait()`
把事件交给 worker 协程处理）。耗时的分析、数据库写入、LLM 调用一律不得写在回调里——
publish 是同步的，慢回调会阻塞发布方。
"""

import threading
from collections import defaultdict
from collections.abc import Callable

from engines.common.logging import logger, mask_secret
from engines.contracts.events import BaseEvent, EventType

# 事件回调：同步、快速、不抛异常为佳（抛了也会被吞掉并记日志）
EventCallback = Callable[[BaseEvent], None]


class EventBus:
    """进程内发布订阅总线。

    线程安全：订阅集合的读写用可重入锁保护。
    发布时先复制订阅者列表再遍历——回调中取消订阅是常见操作，
    直接遍历原集合会抛 "Set changed size during iteration"。
    """

    def __init__(self) -> None:
        self._subscribers: dict[EventType, set[EventCallback]] = defaultdict(set)
        self._lock = threading.RLock()
        self._publish_count: dict[EventType, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------

    def subscribe(self, event_type: EventType, callback: EventCallback) -> None:
        """订阅事件。重复订阅同一回调是幂等的（set 语义）。"""
        with self._lock:
            self._subscribers[event_type].add(callback)

    def unsubscribe(self, event_type: EventType, callback: EventCallback) -> None:
        """取消订阅。对未订阅的回调调用是安全的。"""
        with self._lock:
            self._subscribers[event_type].discard(callback)

    def subscriber_count(self, event_type: EventType) -> int:
        with self._lock:
            return len(self._subscribers.get(event_type, ()))

    # ------------------------------------------------------------------
    # 发布
    # ------------------------------------------------------------------

    def publish(self, event: BaseEvent) -> int:
        """发布事件，返回成功投递的订阅者数量。

        单个订阅者抛异常不会影响其他订阅者——一个观感组件的崩溃不该中断研判链路。
        异常会被记录（含事件类型与角色），便于定位。
        """
        with self._lock:
            callbacks = list(self._subscribers.get(event.event_type, ()))
            self._publish_count[event.event_type] += 1

        delivered = 0
        for callback in callbacks:
            try:
                callback(event)
                delivered += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "事件回调异常 type={} callback={} err={}",
                    event.event_type.value,
                    getattr(callback, "__qualname__", repr(callback)),
                    mask_secret(str(exc))[:200],
                )
        return delivered

    # ------------------------------------------------------------------
    # 运维与测试辅助
    # ------------------------------------------------------------------

    def clear(self, event_type: EventType | None = None) -> None:
        """清空订阅者。不传参数则清空全部——主持人会话重置、测试隔离时使用。"""
        with self._lock:
            if event_type is None:
                self._subscribers.clear()
            else:
                self._subscribers.pop(event_type, None)

    def stats(self) -> dict[str, dict[str, int]]:
        """返回各事件类型的订阅者数与累计发布次数，供诊断接口使用。"""
        with self._lock:
            return {
                et.value: {
                    "subscribers": len(self._subscribers.get(et, ())),
                    "published": self._publish_count.get(et, 0),
                }
                for et in EventType
            }


_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """获取全局事件总线单例。"""
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus


def reset_event_bus() -> None:
    """丢弃全局总线（测试用；生产环境仅在会话重置时调用）。"""
    global _bus
    with _bus_lock:
        _bus = None
