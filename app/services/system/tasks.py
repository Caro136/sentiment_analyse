"""任务运行状态的读写与埋点（FR-72）。

`task_run` 是「谁在什么时候跑了什么、跑了多久、成没成」的唯一事实源。
T-27 的报告任务已经在写它；这里补上**统一埋点入口**，并把预警扫描与大盘刷新
也接进来——否则运维看板上只有一类任务，看不出系统整体在忙什么。

**埋点用上下文管理器而不是装饰器**：任务的状态转移是「开始 → 成功/失败」，
且失败原因要落库。上下文管理器能把「进入即 running、退出即终结」写成一段，
且能在任务体内部随手补充输入摘要（如实际处理了多少条内容）。

失败只记日志、不影响调用方？——**不**。这里与审计写入相反：任务状态是运维要看的
事实，如果连埋点都写不进去，说明库有问题，应当让调用方感知（它本就在做别的事，
失败会一并暴露）。因此 `track_task` 不吞异常。
"""

import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from loguru import logger
from sqlalchemy import func, select

from engines.common.db.models import TaskRun

__all__ = [
    "TASK_TYPES",
    "TaskRecord",
    "TaskRepository",
    "SqlTaskRepository",
    "track_task",
    "new_task_id",
    "summarize",
]

# 任务类型（与 `task_run.task_type` 的 DDL 注释一致）
TASK_TYPES = ("collect", "analyze", "research", "report", "alert", "dashboard")


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    task_type: str
    status: str
    started_at: int
    finished_at: int | None
    duration_ms: int | None
    input_summary: str
    error_message: str | None

    @property
    def running_seconds(self) -> int | None:
        """仍在运行时的已耗时（前端展示「已跑 30 秒」需要它）。"""
        if self.finished_at is not None:
            return None
        return max(0, int(time.time()) - self.started_at)


def new_task_id(task_type: str) -> str:
    """任务标识带类型前缀，便于在日志与 UI 里一眼分清。"""
    import secrets

    return f"task_{task_type}_{int(time.time())}{secrets.token_hex(3)}"


def _to_record(row: TaskRun) -> TaskRecord:
    return TaskRecord(
        task_id=row.task_id, task_type=row.task_type, status=row.status,
        started_at=row.started_at, finished_at=row.finished_at,
        duration_ms=row.duration_ms, input_summary=row.input_summary or "",
        error_message=row.error_message)


class TaskRepository(Protocol):
    """任务状态读写。内存替身与 SQL 实现共用。"""

    async def start(self, *, task_id: str, task_type: str, input_summary: str) -> None: ...
    async def finish(self, task_id: str, *, status: str, error: str = "",
                     duration_ms: int | None = None) -> None: ...
    async def list_tasks(self, *, page: int, page_size: int, task_type: str | None = None,
                         status: str | None = None, start_time: int | None = None,
                         end_time: int | None = None) -> tuple[int, list[TaskRecord]]: ...
    async def counts_by_status(self) -> dict[str, int]: ...
    async def summary_by_type(self) -> list[dict]: ...


class SqlTaskRepository:
    """基于 SQLAlchemy 异步会话的实现。"""

    def __init__(self, session) -> None:
        self._session = session

    async def start(self, *, task_id: str, task_type: str, input_summary: str) -> None:
        self._session.add(TaskRun(
            task_id=task_id, task_type=task_type, status="running",
            started_at=int(time.time()), input_summary=input_summary[:500]))
        await self._session.flush()

    async def finish(self, task_id: str, *, status: str, error: str = "",
                     duration_ms: int | None = None) -> None:
        """终结任务。

        `duration_ms` 由调用方（`track_task`）用 `time.perf_counter()` 量出来传进来。
        **不能用 `finished_at - started_at` 推算**：那两个字段是 Unix **秒**，
        亚秒任务会一律显示 0ms——运维看板上一片「瞬时完成」是假的
        （实测：一次预警扫描显示 0ms）。表结构按约定存秒，精度只能由调用方提供。
        """
        row = (await self._session.execute(
            select(TaskRun).where(TaskRun.task_id == task_id))).scalar_one_or_none()
        if row is None:
            return
        now = int(time.time())
        row.status = status
        row.finished_at = now
        if duration_ms is None:
            # 兜底：调用方没给就用秒级差值（精度差但不会缺失）
            duration_ms = max(0, now - row.started_at) * 1000
        row.duration_ms = max(0, int(duration_ms))
        row.error_message = (error or "")[:500] or None
        await self._session.flush()

    async def list_tasks(self, *, page: int, page_size: int, task_type: str | None = None,
                         status: str | None = None, start_time: int | None = None,
                         end_time: int | None = None,
                         ) -> tuple[int, list[TaskRecord]]:
        conditions = []
        if task_type:
            conditions.append(TaskRun.task_type == task_type)
        if status:
            conditions.append(TaskRun.status == status)
        if start_time is not None:
            conditions.append(TaskRun.started_at >= start_time)
        if end_time is not None:
            conditions.append(TaskRun.started_at <= end_time)

        total_stmt = select(func.count()).select_from(TaskRun)
        rows_stmt = select(TaskRun).order_by(TaskRun.started_at.desc(), TaskRun.id.desc())
        for condition in conditions:
            total_stmt = total_stmt.where(condition)
            rows_stmt = rows_stmt.where(condition)

        total = int((await self._session.execute(total_stmt)).scalar() or 0)
        rows = (await self._session.execute(
            rows_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return total, [_to_record(row) for row in rows]

    async def counts_by_status(self) -> dict[str, int]:
        rows = (await self._session.execute(
            select(TaskRun.status, func.count()).group_by(TaskRun.status))).all()
        return {status: int(count) for status, count in rows}

    async def summary_by_type(self) -> list[dict]:
        """按类型汇总：总次数、成功/失败/运行中、平均耗时（FR-72 的看板主体）。

        平均耗时天然**排除仍在运行的任务**：它们的 `duration_ms` 是 NULL，
        `avg()` 会忽略。否则一个刚起步的任务会把均值拉低，看板数字随刷新时刻抖动。
        用一条 `CASE WHEN` 聚合完成，避免把每个状态捞回来在 Python 里再算一遍。
        """
        from sqlalchemy import case

        rows = (await self._session.execute(
            select(
                TaskRun.task_type,
                func.count(),
                func.sum(case((TaskRun.status == "success", 1), else_=0)),
                func.sum(case((TaskRun.status == "failed", 1), else_=0)),
                func.sum(case((TaskRun.status == "running", 1), else_=0)),
                func.avg(TaskRun.duration_ms),
            ).group_by(TaskRun.task_type)
        )).all()

        summary = [{
            "task_type": task_type,
            "total": int(total or 0),
            "success": int(success or 0),
            "failed": int(failed or 0),
            "running": int(running or 0),
            "avg_duration_ms": (round(float(avg_ms), 1) if avg_ms is not None else None),
        } for task_type, total, success, failed, running, avg_ms in rows]
        return sorted(summary, key=lambda item: item["task_type"])


def summarize(records: list[TaskRecord]) -> dict:
    """对一页任务做即时汇总（供列表响应附带，避免前端再算一遍）。"""
    return {
        "total": len(records),
        "success": sum(1 for r in records if r.status == "success"),
        "failed": sum(1 for r in records if r.status == "failed"),
        "running": sum(1 for r in records if r.status == "running"),
    }


@asynccontextmanager
async def track_task(repo: TaskRepository, *, task_type: str, input_summary: str = "",
                     task_id: str | None = None):
    """任务埋点：进入即 running，退出即 success/failed。

    用法（注意它**要用自己的会话**，原因同 T-27 的后台任务）：

        async with session_scope() as session:
            async with track_task(SqlTaskRepository(session), task_type="alert") as task:
                ...

    异常照常抛出：调用方该失败就失败，埋点只负责把「失败」如实记下来。
    """
    identifier = task_id or new_task_id(task_type)
    await repo.start(task_id=identifier, task_type=task_type,
                     input_summary=input_summary)
    started = time.perf_counter()
    try:
        yield identifier
    except BaseException as exc:  # 含 CancelledError：也要留下「中断」的痕迹
        await repo.finish(identifier, status="failed",
                          error=f"{type(exc).__name__}: {exc}",
                          duration_ms=_elapsed_ms(started))
        raise
    else:
        await repo.finish(identifier, status="success",
                          duration_ms=_elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    """毫秒耗时。用 perf_counter 而不是秒级时间戳（见 `finish` 的说明）。"""
    return max(0, int((time.perf_counter() - started) * 1000))
