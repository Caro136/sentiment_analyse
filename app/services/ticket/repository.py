"""处置工单仓储（`ticket` / `ticket_log`）。

**轨迹只在 `ticket_log` 里长**：工单表存当前状态，日志表存「怎么走到这一步」。
两者必须同时写、同事务提交——只改状态不记轨迹，等于把处置过程丢了（FR-50）。
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select

from engines.common.db.models import Ticket, TicketLog

__all__ = [
    "TicketRecord",
    "TicketLogRecord",
    "TicketRepository",
    "SqlTicketRepository",
    "new_ticket_id",
]


@dataclass(frozen=True)
class TicketRecord:
    ticket_id: str
    event_id: str
    alert_id: str | None
    status: str
    assignee_id: str | None
    risk_level: str | None
    response_draft: str | None
    created_at: int
    closed_at: int | None


@dataclass(frozen=True)
class TicketLogRecord:
    action: str
    from_status: str | None
    to_status: str | None
    operator_id: str
    note: str
    operated_at: int


def new_ticket_id() -> str:
    import secrets

    return f"tk{int(time.time())}{secrets.token_hex(3)}"


def _to_ticket(row: Ticket) -> TicketRecord:
    return TicketRecord(
        ticket_id=row.ticket_id, event_id=row.event_id, alert_id=row.alert_id,
        status=row.status, assignee_id=row.assignee_id, risk_level=row.risk_level,
        response_draft=row.response_draft, created_at=row.created_at,
        closed_at=row.closed_at)


def _to_log(row: TicketLog) -> TicketLogRecord:
    return TicketLogRecord(action=row.action, from_status=row.from_status,
                           to_status=row.to_status, operator_id=row.operator_id,
                           note=row.note or "", operated_at=row.operated_at)


class TicketRepository(Protocol):
    """工单读写接口。内存替身与 SQL 实现共用。"""

    async def create_ticket(self, record: TicketRecord) -> TicketRecord: ...
    async def get_ticket(self, ticket_id: str) -> TicketRecord | None: ...
    async def find_open_by_event(self, event_id: str) -> TicketRecord | None: ...
    async def list_tickets(self, *, page: int, page_size: int, status: str | None = None,
                           event_id: str | None = None, assignee_id: str | None = None,
                           ) -> tuple[int, list[TicketRecord]]: ...
    async def update_ticket(self, ticket_id: str, fields: dict) -> TicketRecord | None: ...
    async def add_log(self, ticket_id: str, record: TicketLogRecord) -> None: ...
    async def list_logs(self, ticket_id: str) -> list[TicketLogRecord]: ...


class SqlTicketRepository:
    """基于 SQLAlchemy 异步会话的实现。事务边界由调用方决定。"""

    def __init__(self, session) -> None:
        self._session = session

    async def create_ticket(self, record: TicketRecord) -> TicketRecord:
        row = Ticket(ticket_id=record.ticket_id, event_id=record.event_id,
                     alert_id=record.alert_id, status=record.status,
                     assignee_id=record.assignee_id, risk_level=record.risk_level,
                     response_draft=record.response_draft, created_at=record.created_at,
                     closed_at=record.closed_at)
        self._session.add(row)
        await self._session.flush()
        return _to_ticket(row)

    async def get_ticket(self, ticket_id: str) -> TicketRecord | None:
        row = (await self._session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id))).scalar_one_or_none()
        return _to_ticket(row) if row else None

    async def find_open_by_event(self, event_id: str) -> TicketRecord | None:
        """该事件下尚未关闭的工单（防止同一事件重复开单）。"""
        row = (await self._session.execute(
            select(Ticket).where(Ticket.event_id == event_id,
                                 Ticket.status.notin_(("resolved", "ignored")))
            .order_by(Ticket.created_at.desc()).limit(1))).scalar_one_or_none()
        return _to_ticket(row) if row else None

    async def list_tickets(self, *, page: int, page_size: int, status: str | None = None,
                           event_id: str | None = None, assignee_id: str | None = None,
                           ) -> tuple[int, list[TicketRecord]]:
        conditions = []
        if status:
            conditions.append(Ticket.status == status)
        if event_id:
            conditions.append(Ticket.event_id == event_id)
        if assignee_id:
            conditions.append(Ticket.assignee_id == assignee_id)

        total_stmt = select(func.count()).select_from(Ticket)
        rows_stmt = select(Ticket).order_by(Ticket.created_at.desc(), Ticket.id.desc())
        for condition in conditions:
            total_stmt = total_stmt.where(condition)
            rows_stmt = rows_stmt.where(condition)

        total = int((await self._session.execute(total_stmt)).scalar() or 0)
        rows = (await self._session.execute(
            rows_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return total, [_to_ticket(row) for row in rows]

    async def update_ticket(self, ticket_id: str, fields: dict) -> TicketRecord | None:
        row = (await self._session.execute(
            select(Ticket).where(Ticket.ticket_id == ticket_id))).scalar_one_or_none()
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        return _to_ticket(row)

    async def add_log(self, ticket_id: str, record: TicketLogRecord) -> None:
        self._session.add(TicketLog(
            ticket_id=ticket_id, action=record.action, from_status=record.from_status,
            to_status=record.to_status, operator_id=record.operator_id,
            note=record.note or None, operated_at=record.operated_at))
        await self._session.flush()

    async def list_logs(self, ticket_id: str) -> list[TicketLogRecord]:
        rows = (await self._session.execute(
            select(TicketLog).where(TicketLog.ticket_id == ticket_id)
            .order_by(TicketLog.operated_at, TicketLog.id))).scalars().all()
        return [_to_log(row) for row in rows]


def snapshot(record: TicketRecord) -> dict[str, Any]:
    """审计用的工单快照（不含草案全文——草案可能有上千字，审计行只记长度）。"""
    return {
        "ticket_id": record.ticket_id,
        "event_id": record.event_id,
        "status": record.status,
        "assignee_id": record.assignee_id,
        "risk_level": record.risk_level,
        "has_draft": bool(record.response_draft),
        "draft_chars": len(record.response_draft or ""),
        "closed_at": record.closed_at,
    }
