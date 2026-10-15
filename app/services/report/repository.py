"""报告与产物的仓储（`report` / `report_file` / `task_run`）。

两条约定：

1. **报告状态与产物文件分两张表**：`report` 存「这一份报告是什么、成没成」，
   `report_file` 存「产物落在哪、内容是什么」。同一份报告可能有 md 与 html 两个文件，
   且**正文同时存库**（`content_md`）——预览不必读磁盘，
   磁盘文件被清理或目录迁移后报告依然可看。
2. **任务状态写 `task_run`**：报告生成是长任务（实测约 90 秒），
   「谁在什么时候跑了多久、成没成」按 FR-72 的口径落在任务表里，
   供 T-28 的系统运维页检索，也便于回答「为什么报告等了很久」。
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select

from engines.common.db.models import Report, ReportFile, TaskRun

__all__ = [
    "ReportRecord",
    "ReportFileRecord",
    "ReportRepository",
    "SqlReportRepository",
    "new_report_id",
    "new_file_id",
    "new_task_id",
]


@dataclass(frozen=True)
class ReportRecord:
    report_id: str
    event_id: str | None
    topic_id: str | None
    report_type: str
    title: str
    status: str
    error_message: str | None
    generated_at: int | None
    created_by: str | None
    created_at: int


@dataclass(frozen=True)
class ReportFileRecord:
    file_id: str
    report_id: str
    file_type: str
    file_path: str
    content_md: str | None
    size_bytes: int | None
    created_at: int


def new_report_id() -> str:
    import secrets

    return f"rpt{int(time.time())}{secrets.token_hex(3)}"


def new_file_id() -> str:
    import secrets

    return f"rf{int(time.time())}{secrets.token_hex(3)}"


def new_task_id() -> str:
    import secrets

    return f"task_report_{int(time.time())}{secrets.token_hex(3)}"


def _to_report(row: Report) -> ReportRecord:
    return ReportRecord(
        report_id=row.report_id, event_id=row.event_id, topic_id=row.topic_id,
        report_type=row.report_type, title=row.title, status=row.status,
        error_message=row.error_message, generated_at=row.generated_at,
        created_by=row.created_by, created_at=row.created_at)


def _to_file(row: ReportFile) -> ReportFileRecord:
    return ReportFileRecord(
        file_id=row.file_id, report_id=row.report_id, file_type=row.file_type,
        file_path=row.file_path, content_md=row.content_md,
        size_bytes=row.size_bytes, created_at=row.created_at)


class ReportRepository(Protocol):
    """报告读写接口。内存替身与 SQL 实现共用。"""

    async def create_report(self, record: ReportRecord) -> ReportRecord: ...
    async def get_report(self, report_id: str) -> ReportRecord | None: ...
    async def find_active(self, *, event_id: str | None = None,
                          topic_id: str | None = None,
                          title: str | None = None) -> ReportRecord | None: ...
    async def list_reports(self, *, page: int, page_size: int, topic_id: str | None = None,
                           event_id: str | None = None, status: str | None = None,
                           ) -> tuple[int, list[ReportRecord]]: ...
    async def update_report(self, report_id: str, fields: dict) -> ReportRecord | None: ...
    async def add_file(self, record: ReportFileRecord) -> ReportFileRecord: ...
    async def list_files(self, report_id: str) -> list[ReportFileRecord]: ...
    async def start_task(self, *, task_id: str, task_type: str,
                         input_summary: str) -> None: ...
    async def finish_task(self, task_id: str, *, status: str, error: str = "") -> None: ...
    async def mark_interrupted_tasks(self, reason: str) -> int: ...


class SqlReportRepository:
    """基于 SQLAlchemy 异步会话的实现。"""

    def __init__(self, session) -> None:
        self._session = session

    async def create_report(self, record: ReportRecord) -> ReportRecord:
        row = Report(report_id=record.report_id, event_id=record.event_id,
                     topic_id=record.topic_id, report_type=record.report_type,
                     title=record.title, status=record.status,
                     error_message=record.error_message, generated_at=record.generated_at,
                     created_by=record.created_by, created_at=record.created_at)
        self._session.add(row)
        await self._session.flush()
        return _to_report(row)

    async def get_report(self, report_id: str) -> ReportRecord | None:
        row = (await self._session.execute(
            select(Report).where(Report.report_id == report_id))).scalar_one_or_none()
        return _to_report(row) if row else None

    async def find_active(self, *, event_id: str | None = None,
                          topic_id: str | None = None,
                          title: str | None = None) -> ReportRecord | None:
        """同一事件/主题（或同一标题）是否已有未完成的报告。

        三个条件按优先级取其一：事件 → 主题 → 标题。标题是最后一道兜底——
        只给 query 的试跑路径没有事件也没有主题，但标题由 query 确定性派生，
        足以识别「同一个主题连点两次」。
        """
        conditions = [Report.status == "generating"]
        if event_id:
            conditions.append(Report.event_id == event_id)
        elif topic_id:
            conditions.append(Report.topic_id == topic_id)
        elif title:
            conditions.append(Report.title == title)
        else:
            return None

        row = (await self._session.execute(
            select(Report).where(*conditions)
            .order_by(Report.created_at.desc()).limit(1))).scalar_one_or_none()
        return _to_report(row) if row else None

    async def list_reports(self, *, page: int, page_size: int, topic_id: str | None = None,
                           event_id: str | None = None, status: str | None = None,
                           ) -> tuple[int, list[ReportRecord]]:
        conditions = []
        if topic_id:
            conditions.append(Report.topic_id == topic_id)
        if event_id:
            conditions.append(Report.event_id == event_id)
        if status:
            conditions.append(Report.status == status)

        total_stmt = select(func.count()).select_from(Report)
        rows_stmt = select(Report).order_by(Report.created_at.desc(), Report.id.desc())
        for condition in conditions:
            total_stmt = total_stmt.where(condition)
            rows_stmt = rows_stmt.where(condition)

        total = int((await self._session.execute(total_stmt)).scalar() or 0)
        rows = (await self._session.execute(
            rows_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return total, [_to_report(row) for row in rows]

    async def update_report(self, report_id: str, fields: dict) -> ReportRecord | None:
        row = (await self._session.execute(
            select(Report).where(Report.report_id == report_id))).scalar_one_or_none()
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        return _to_report(row)

    async def add_file(self, record: ReportFileRecord) -> ReportFileRecord:
        row = ReportFile(file_id=record.file_id, report_id=record.report_id,
                         file_type=record.file_type, file_path=record.file_path,
                         content_md=record.content_md, size_bytes=record.size_bytes,
                         created_at=record.created_at)
        self._session.add(row)
        await self._session.flush()
        return _to_file(row)

    async def list_files(self, report_id: str) -> list[ReportFileRecord]:
        rows = (await self._session.execute(
            select(ReportFile).where(ReportFile.report_id == report_id)
            .order_by(ReportFile.id))).scalars().all()
        return [_to_file(row) for row in rows]

    async def start_task(self, *, task_id: str, task_type: str,
                         input_summary: str) -> None:
        self._session.add(TaskRun(
            task_id=task_id, task_type=task_type, status="running",
            started_at=int(time.time()), input_summary=input_summary[:500]))
        await self._session.flush()

    async def finish_task(self, task_id: str, *, status: str, error: str = "",
                          duration_ms: int | None = None) -> None:
        """终结任务。耗时优先用调用方量出的毫秒值（见 `system.tasks.finish` 的说明：
        秒级时间戳推算会让亚秒任务显示 0ms）。"""
        row = (await self._session.execute(
            select(TaskRun).where(TaskRun.task_id == task_id))).scalar_one_or_none()
        if row is None:
            return
        now = int(time.time())
        row.status = status
        row.finished_at = now
        if duration_ms is None:
            duration_ms = max(0, now - row.started_at) * 1000
        row.duration_ms = max(0, int(duration_ms))
        row.error_message = (error or "")[:500] or None
        await self._session.flush()

    async def mark_interrupted_tasks(self, reason: str) -> int:
        """把所有仍标记为 `running` 的任务置为失败（进程重启后的收尾）。

        运行中的任务随进程消失，留下的 `running` 行是**假的实时状态**——
        运维页若照着它显示「正在运行」，排查方向从一开始就是错的。
        """
        rows = (await self._session.execute(
            select(TaskRun).where(TaskRun.status == "running"))).scalars().all()
        now = int(time.time())
        for row in rows:
            row.status = "failed"
            row.finished_at = now
            row.duration_ms = max(0, (now - row.started_at)) * 1000
            row.error_message = reason[:500]
        await self._session.flush()
        return len(rows)


def snapshot(record: ReportRecord, *, files: list[ReportFileRecord] | None = None) -> dict[str, Any]:
    """审计用的报告快照（不含正文：报告可能上万字，审计行只记规模与文件）。"""
    return {
        "report_id": record.report_id,
        "event_id": record.event_id,
        "topic_id": record.topic_id,
        "report_type": record.report_type,
        "title": record.title,
        "status": record.status,
        "files": [{"file_type": f.file_type, "size_bytes": f.size_bytes}
                  for f in (files or [])],
        "error": (record.error_message or "")[:200] or None,
    }


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
