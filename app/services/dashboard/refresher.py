"""大盘指标的定时刷新任务（设计规格 8.2：每 60 秒物化一次）。

与预警监听器同一套模式：跟随应用生命周期启停、失败只记日志不影响主流程、
进程内单例——因此同样要求单 worker（ADR-05）。

**为什么要后台任务而不是「请求时算」**：大盘首屏要 < 3s，而聚合查询会随数据量线性
变慢；物化把这份成本摊到后台，读路径只剩一次索引查询。

任务里的失败处理是刻意的：**失败不中断循环**，下一轮重试。指标刷新失败只意味着
大盘数字暂时是旧的（读路径发现过期还会同步补一次），不该让任务协程直接死掉。
"""

import asyncio

from loguru import logger

from app.services.dashboard import service as dashboard_service
from app.services.dashboard.repository import SqlDashboardRepository
from engines.common.db.session import session_scope

__all__ = ["DashboardRefresher", "get_refresher", "reset_refresher"]


class DashboardRefresher:
    """周期性刷新大盘指标的协程任务。"""

    def __init__(self, *, interval_seconds: int |
                 None = None) -> None:
        self._interval = (interval_seconds
                          or dashboard_service.REFRESH_INTERVAL_SECONDS)
        self._task: asyncio.Task | None = None
        self._rounds = 0
        self._failures = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def stats(self) -> dict:
        return {"running": self.running, "rounds": self._rounds,
                "failures": self._failures, "interval_seconds": self._interval}

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 —— 一轮失败不影响下一轮
                self._failures += 1
                logger.opt(exception=True).warning("大盘指标刷新失败，等待下一轮重试")
            await asyncio.sleep(self._interval)

    async def refresh_once(self) -> dict[str, int]:
        """刷新全局与全部启用主题的指标。

        每轮写一条 `task_run`（FR-72）：大盘刷新是最频繁的后台动作，
        运维看板上没有它就看不出「系统一直在忙什么」。
        """
        from app.services.content.repository import SqlContentRepository
        from app.services.event.repository import SqlEventRepository
        from app.services.monitoring.repository import SqlMonitoringRepository
        from app.services.system.tasks import SqlTaskRepository, track_task
        from engines.contracts.enums import TopicStatus

        async with session_scope() as session:
            repo = SqlDashboardRepository(session)
            monitoring = SqlMonitoringRepository(session)
            _, topics = await monitoring.list_topics(
                page=1, page_size=200, status=TopicStatus.ENABLED.value)
            topic_ids = [topic.topic_id for topic in topics]

            async with track_task(SqlTaskRepository(session), task_type="dashboard",
                                  input_summary=f"topics={len(topic_ids)}"):
                result = await dashboard_service.refresh_all(
                    repo, topic_ids=topic_ids,
                    event_repo=SqlEventRepository(session),
                    content_repo=SqlContentRepository(session),
                    session=session)

        self._rounds += 1
        return result

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("大盘指标刷新任务已启动（每 {} 秒一轮）", self._interval)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
            logger.info("大盘指标刷新任务已停止")


_refresher: DashboardRefresher | None = None


def get_refresher() -> DashboardRefresher:
    global _refresher
    if _refresher is None:
        _refresher = DashboardRefresher()
    return _refresher


def reset_refresher() -> None:
    """丢弃单例（测试用）。"""
    global _refresher
    if _refresher is not None:
        _refresher.stop()
    _refresher = None
