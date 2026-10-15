"""预警监听器：订阅「内容已打标」事件，驱动规则扫描。

**回调必须 O(1)**（事件总线的硬约束，见 `common/eventing/bus.py`）：发布方此刻
正在执行打标流程，回调里做数据库扫描会把它一起拖住。因此回调只做
`put_nowait`，真正的工作交给 worker 协程串行处理。

**合并积压**：一次批量打标可能连着发布多个事件（按批），worker 会把它们折叠成
一次扫描——同一批内容重复扫几遍没有意义，反而会让收敛计数虚高。
"""

import asyncio

from loguru import logger

from engines.alerting import service as alert_service
from engines.common.db.session import session_scope
from engines.common.eventing.bus import get_event_bus
from engines.contracts.events import ContentLabeledEvent, EventType

__all__ = ["AlertListener", "get_listener", "reset_listener"]


class AlertListener:
    """事件 → 扫描的单向桥。"""

    def __init__(self, *, queue_size: int = 64) -> None:
        self._queue: asyncio.Queue[ContentLabeledEvent] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task | None = None
        self._queue_full = 0

    # -- 订阅方（同步、O(1)）--

    def enqueue(self, event: ContentLabeledEvent) -> None:
        """事件回调：只入队。队列满时丢弃并计数——预警是「多报一条 vs 卡死打标」，
        这里选择不卡死（丢事件的代价由下一次扫描补上）。"""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._queue_full += 1
            logger.warning("预警队列已满，丢弃事件（累计 {} 次）；下次扫描会补上",
                           self._queue_full)

    # -- worker --

    async def _worker(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                # 折叠积压：把队列里其余事件一并取走，只扫一次
                drained = 1
                while not self._queue.empty():
                    self._queue.get_nowait()
                    drained += 1
                    self._queue.task_done()
                await self._handle(event, drained)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 预警失败不能拖垮监听循环
                logger.opt(exception=True).error("预警扫描失败，已跳过本次事件")
            finally:
                self._queue.task_done()

    async def _handle(self, event: ContentLabeledEvent, drained: int) -> None:
        logger.info("收到打标事件：{} 条内容 / 主题 {}（折叠 {} 个事件），开始预警扫描",
                    len(event.content_uids), list(event.topic_ids) or "未归属", drained)
        async with session_scope() as session:
            result = await alert_service.scan_event(event, session)
        logger.info(result.render())

    # -- 生命周期 --

    def start(self) -> None:
        if self._task is not None:
            return
        get_event_bus().subscribe(EventType.CONTENT_LABELED, self.enqueue)
        self._task = asyncio.create_task(self._worker())
        logger.info("预警监听器已启动（订阅 {}）", EventType.CONTENT_LABELED.value)

    def stop(self) -> None:
        get_event_bus().unsubscribe(EventType.CONTENT_LABELED, self.enqueue)
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("预警监听器已停止")


_listener: AlertListener | None = None


def get_listener() -> AlertListener:
    global _listener
    if _listener is None:
        _listener = AlertListener()
    return _listener


def reset_listener() -> None:
    """丢弃单例（测试与配置热更新用）。"""
    global _listener
    if _listener is not None:
        _listener.stop()
    _listener = None
