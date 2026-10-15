"""章节就绪事件监听器。

职责：把事件流变成串行的图调用。

**回调必须 O(1)**：事件的发布方（两个研究员的章节生成节点）正在同步执行，
回调里做任何耗时操作都会阻塞它。所以回调只做 `put_nowait`，
真正的图调用交给 worker 协程串行处理。

**为什么串行而不是并发**：主持人的状态是共享的（PairStore 与研判列表），
并发处理多个事件需要加锁，而事件量本来就小（两次来源 × 五个维度 = 10 次），
串行的吞吐完全够用，换来的是零并发 bug。
"""

import asyncio

from engines.common.eventing.bus import get_event_bus
from engines.common.logging import logger, mask_secret
from engines.contracts.events import EventType, SectionReadyEvent
from engines.contracts.section import SectionDraft
from engines.host_agent.graph import DEFAULT_RECURSION_LIMIT, get_graph
from engines.host_agent.state import HostSession


class SectionReadyListener:
    """订阅 SECTION_READY，按到达顺序串行驱动主持人图。"""

    def __init__(self, session: HostSession) -> None:
        self._session = session
        self._queue: asyncio.Queue[SectionReadyEvent] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._bus = get_event_bus()
        self.processed = 0
        self.skipped = 0

    # ------------------------------------------------------------------

    def start(self) -> None:
        """订阅事件并启动 worker。"""
        if self._worker is not None:
            return
        self._bus.subscribe(EventType.SECTION_READY, self._on_section_ready)
        self._worker = asyncio.create_task(self._consume(),
                                           name="host-agent-worker")
        logger.info("主持人监听器已启动")

    async def stop(self) -> None:
        """停止监听。会清空会话——重启意味着换了一个研究主题。"""
        if self._worker is None:
            return
        self._bus.unsubscribe(EventType.SECTION_READY, self._on_section_ready)
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None
        self._drain()
        logger.info("主持人监听器已停止（处理 {} 条事件）", self.processed)

    # ------------------------------------------------------------------

    def _on_section_ready(self, event) -> None:
        """事件回调。**只入队，不做任何耗时操作。**"""
        if isinstance(event, SectionReadyEvent):
            self._queue.put_nowait(event)

    async def _consume(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("主持人处理事件失败：{}",
                                 mask_secret(str(exc))[:160])

    async def _handle(self, event: SectionReadyEvent) -> None:
        # ---- 跨主题隔离 ----
        # 配对是按 section_key 做的。上一个主题的章节如果混进来，
        # 会与当前主题的同名维度错误配对，产出张冠李戴的研判结论。
        if self._session.query and event.query and event.query != self._session.query:
            self.skipped += 1
            logger.debug("忽略其他主题的章节：{} != {}",
                         event.query, self._session.query)
            return

        if not self._session.query and event.query:
            self._session.query = event.query

        draft = SectionDraft(
            section_key=event.section_key,
            index=event.section_index,
            title=event.title,
            body=event.body,
            strength=str(event.section_metadata.get("strength", "")),
            evidence_count=int(event.section_metadata.get("hit_count", 0) or 0),
        )

        state = self._session.to_state(draft, event.source)
        result = await get_graph().ainvoke(
            state, {"recursion_limit": DEFAULT_RECURSION_LIMIT})
        self._session.apply_state(result)
        self.processed += 1

    def _drain(self) -> None:
        """清空积压事件并重置会话。"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._session.clear()

    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def pending(self) -> int:
        return self._queue.qsize()
