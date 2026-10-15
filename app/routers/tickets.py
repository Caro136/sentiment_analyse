"""处置工单路由（API-16~18，FR-49~FR-51）。

权限分三层（PRD 第 2 章）：
  - 查看工单与轨迹：`ticket:read`（分析师 + 公关）
  - 开单与流转、批注、生成/采纳草案：`ticket:flow` / `draft:write`（分析师 + 公关）
  - **关闭工单（已处理 / 已忽略）：`ticket:close`（仅公关）**——同一 PUT 接口，
    按目标状态动态选权限依赖，见 `_resolve_flow_guard`
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.schemas.ticket import (
    DraftAdoptRequest,
    ResponseDraftRequest,
    ResponseDraftView,
    TicketCreateRequest,
    TicketDetailView,
    TicketQuery,
    TicketUpdateRequest,
    TicketView,
)
from app.errors import forbidden
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.content.repository import ContentRepository, SqlContentRepository
from app.services.event.repository import EventRepository, SqlEventRepository
from app.services.ticket import lifecycle
from app.services.ticket import service
from app.services.ticket.repository import SqlTicketRepository, TicketRepository
from engines.common.db.session import get_db_session
from engines.contracts.enums import TicketStatus
from engines.contracts.permissions import Permission, can

router = APIRouter(prefix="/api/v1/tickets", tags=["tickets"])
# 草案生成的路径按设计规格挂在事件下（API-16：POST /events/{id}/response-draft）：
# 「对某个事件生成回应草案」在语义上属于事件的操作，而不是工单的操作。
event_router = APIRouter(prefix="/api/v1/events", tags=["tickets"])

_ReadTicket = Annotated[UserRecord, Depends(require_permission(Permission.TICKET_READ))]
_FlowTicket = Annotated[UserRecord, Depends(require_permission(Permission.TICKET_FLOW))]
_DraftTicket = Annotated[UserRecord, Depends(require_permission(Permission.DRAFT_WRITE))]


async def get_ticket_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlTicketRepository:
    return SqlTicketRepository(session)


async def get_event_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlEventRepository:
    return SqlEventRepository(session)


async def get_content_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlContentRepository:
    """草案生成要读内容标签（与事件详情同一套读取）。"""
    return SqlContentRepository(session)


def _require_flow_permission(target_status: str, actor: UserRecord) -> None:
    """按**目标状态**判断是否需要额外权限：关闭动作要求 `ticket:close`。

    写成显式判断而不是两条路由，是因为「关闭」在路径上无法区分——
    都是 `PUT /tickets/{id}`，仅请求体里的 status 不同，而权限依赖在路由声明处
    无法按请求体分支。服务层还有第二道同类检查（不依赖调用方自觉）。
    """
    if not lifecycle.is_closing(target_status):
        return
    if not can(actor.role, Permission.TICKET_CLOSE):
        raise forbidden(Permission.TICKET_CLOSE.value, detail=f"role={actor.role}")


@router.get("", response_model=PageResult[TicketView], summary="工单列表")
async def list_tickets(
    _actor: _ReadTicket,
    repo=Depends(get_ticket_repository),
    status: TicketStatus | None = Query(default=None, description="按状态筛选"),
    event_id: str | None = Query(default=None, max_length=64),
    assignee_id: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[TicketView]:
    query = TicketQuery(page=page, page_size=page_size, status=status,
                        event_id=event_id, assignee_id=assignee_id)
    total, items = await service.list_tickets(
        repo, page=query.page, page_size=query.page_size,
        status=query.status.value if query.status else None,
        event_id=query.event_id, assignee_id=query.assignee_id)
    return paginate(total, [TicketView(**item) for item in items], query.page, query.page_size)


@router.post("", response_model=TicketDetailView, status_code=201, summary="创建处置工单")
async def create_ticket(payload: TicketCreateRequest, actor: _FlowTicket,
                        repo=Depends(get_ticket_repository),
                        event_repo=Depends(get_event_repository),
                        content_repo=Depends(get_content_repository),
                        session: AsyncSession = Depends(get_db_session)) -> TicketDetailView:
    """为事件开一张工单（FR-49）。同一事件只允许一张未关闭的工单。

    `risk_level` 留空时取事件的生效风险（人工复核过即人工值）。
    """
    detail = await service.create_ticket(
        repo, event_repo, content_repo, session, actor=actor, event_id=payload.event_id,
        alert_id=payload.alert_id,
        risk_level=payload.risk_level.value if payload.risk_level else None,
        assignee_id=payload.assignee_id, note=payload.note)
    return TicketDetailView(**detail)


@router.get("/{ticket_id}", response_model=TicketDetailView, summary="工单详情与处置轨迹")
async def get_ticket(ticket_id: str, _actor: _ReadTicket,
                     repo=Depends(get_ticket_repository)) -> TicketDetailView:
    """含按时间正序的处置轨迹（FR-50）。"""
    return TicketDetailView(**await service.get_ticket_detail(repo, ticket_id))


@router.put("/{ticket_id}", response_model=TicketDetailView, summary="流转 / 认领 / 批注")
async def update_ticket(ticket_id: str, payload: TicketUpdateRequest, actor: _FlowTicket,
                        repo=Depends(get_ticket_repository)) -> TicketDetailView:
    """状态流转、认领、追加批注（FR-49/50）。

    - 只传 `note` → 追加一条批注轨迹
    - 传 `status` → 状态机校验（非法迁移 409，终态不可流转）
    - 目标为 `resolved` / `ignored` → **需要 `ticket:close`（仅公关）**
    """
    if payload.status is not None:
        _require_flow_permission(payload.status.value, actor)

    detail = await service.update_ticket(
        repo, actor=actor, ticket_id=ticket_id,
        status=payload.status.value if payload.status else None,
        assignee_id=payload.assignee_id,
        risk_level=payload.risk_level.value if payload.risk_level else None,
        note=payload.note)
    return TicketDetailView(**detail)


@event_router.post("/{event_id}/response-draft", response_model=ResponseDraftView,
                   summary="生成公关回应草案")
async def generate_response_draft(
    event_id: str, payload: ResponseDraftRequest, _actor: _DraftTicket,
    event_repo: EventRepository = Depends(get_event_repository),
    content_repo: ContentRepository = Depends(get_content_repository),
    session: AsyncSession = Depends(get_db_session),
) -> ResponseDraftView:
    """生成三段式草案（事实澄清 / 情绪疏导 / 行动方案，FR-51）。

    **不落库**：生成是探索动作，可能反复试；采纳后才写进工单。
    模型输出可能包含未经核实的事实或承诺，务必人工确认后再采纳。
    """
    result = await service.generate_response_draft(
        event_repo, content_repo, session, event_id=event_id,
        cluster_labels=payload.cluster_labels)
    return ResponseDraftView(**result)


@router.post("/{ticket_id}/draft", response_model=TicketDetailView, summary="采纳回应草案")
async def adopt_response_draft(ticket_id: str, payload: DraftAdoptRequest,
                               actor: _DraftTicket,
                               repo=Depends(get_ticket_repository)) -> TicketDetailView:
    """把草案（可编辑后）写入工单并记 `draft_adopt` 轨迹（FR-51）。"""
    detail = await service.adopt_response_draft(
        repo, actor=actor, ticket_id=ticket_id, content=payload.content,
        note=payload.note)
    return TicketDetailView(**detail)
