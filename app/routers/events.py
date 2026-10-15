"""事件与溯源路由（API-10~14，FR-45~FR-48）。

路径顺序：`/events/{event_id}` 与 `/events/{event_id}/...` 都带路径参数，
但 `GET /events` 无参数，因此不存在 `/check` 那类遮蔽问题（见 channels.py 的说明）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.schemas.content import ContentItem
from app.schemas.event import (
    ClusterListView,
    EventDetail,
    EventQuery,
    EventSummary,
    GraphView,
    RiskOverrideRequest,
    TimelineView,
)
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.content.repository import ContentRepository, SqlContentRepository
from app.services.event import service
from app.services.event.repository import EventRepository, SqlEventRepository
from engines.common.db.session import get_db_session
from engines.contracts.enums import EventStatus, RiskLevel
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/events", tags=["events"])

_ReadEvent = Annotated[UserRecord, Depends(require_permission(Permission.EVENT_READ))]
# 风险复核**仅分析师**（2026-10-15 确认）：管理员不参与业务结论的修改
_WriteRisk = Annotated[UserRecord, Depends(require_permission(Permission.RISK_OVERRIDE))]


async def get_event_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlEventRepository:
    return SqlEventRepository(session)


async def get_content_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlContentRepository:
    """事件视图要读内容标签（统计、簇代表言论），复用内容查询的仓储。"""
    return SqlContentRepository(session)


@router.get("", response_model=PageResult[EventSummary], summary="事件专题列表")
async def list_events(
    _actor: _ReadEvent,
    repo=Depends(get_event_repository),
    content_repo=Depends(get_content_repository),
    session: AsyncSession = Depends(get_db_session),
    topic_id: str | None = Query(default=None, max_length=64),
    risk_level: RiskLevel | None = Query(default=None, description="按事件最高风险等级筛选"),
    status: EventStatus | None = Query(default=None, description="事件状态"),
    keyword: str | None = Query(default=None, max_length=64, description="按标题模糊匹配"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[EventSummary]:
    """事件专题列表（FR-48）。

    风险等级、声量、负面率、时间范围都是**由关联内容现算**的（`event` 表的聚合列是空的），
    因此 `risk_level` 筛选筛的是「该事件内容的最高风险」，与列表展示的值一致。
    排序按最近发布时间倒序。
    """
    total, items = await service.list_events(
        repo, content_repo, session, topic_id=topic_id,
        risk_level=risk_level.value if risk_level else None,
        status=status.value if status else None, keyword=keyword,
        page=page, page_size=page_size)
    return paginate(total, [EventSummary(**item) for item in items], page, page_size)


@router.get("/{event_id}", response_model=EventDetail, summary="事件详情")
async def get_event(event_id: str, _actor: _ReadEvent,
                    repo=Depends(get_event_repository),
                    content_repo=Depends(get_content_repository),
                    session: AsyncSession = Depends(get_db_session)) -> EventDetail:
    """事件详情：统计 + 聚类数 + 图谱规模。"""
    return EventDetail(**await service.get_event_detail(repo, content_repo, session,
                                                       event_id))


@router.put("/{event_id}/risk-level", response_model=EventDetail, summary="风险等级人工复核")
async def override_risk_level(
    event_id: str, payload: RiskOverrideRequest, actor: _WriteRisk,
    repo=Depends(get_event_repository),
    content_repo=Depends(get_content_repository),
    session: AsyncSession = Depends(get_db_session),
) -> EventDetail:
    """人工覆盖事件风险等级，需填写理由（FR-24，**权限仅分析师**）。

    算法原始结论不会丢：表里存人工值，算法的值每次由关联内容现算，
    响应里 `risk_level`（生效）与 `algorithm_risk_level`（算法）同时给出。
    重复提交同一个等级不产生变更记录。
    """
    detail = await service.override_risk_level(
        repo, content_repo, session, actor=actor, event_id=event_id,
        risk_level=payload.risk_level.value, reason=payload.reason)
    return EventDetail(**detail)


@router.get("/{event_id}/timeline", response_model=TimelineView, summary="传播溯源时间线")
async def get_timeline(
    event_id: str, _actor: _ReadEvent,
    repo=Depends(get_event_repository),
    content_repo=Depends(get_content_repository),
    session: AsyncSession = Depends(get_db_session),
    node_scope: str = Query(default="key", pattern="^(key|all)$",
                            description="key 只给关键节点，all 给全部内容"),
) -> TimelineView:
    """按发布时间**正序**的传播脉络，标注首发、引爆、官方回应与媒体跟进（FR-45）。

    节点角色由发布时间、账号认证与影响力、信源类型**推导**（`role_source=derived`），
    因为 `spread_node` 表尚未落数据；默认只返回关键节点，事件内容上千条时
    `node_scope=all` 会把全部内容铺开。
    """
    return TimelineView(**await service.build_timeline(repo, content_repo, session,
                                                      event_id, node_scope=node_scope))


@router.get("/{event_id}/clusters", response_model=ClusterListView, summary="观点聚类")
async def get_clusters(event_id: str, _actor: _ReadEvent,
                       repo=Depends(get_event_repository),
                       content_repo=Depends(get_content_repository),
                       session: AsyncSession = Depends(get_db_session)) -> ClusterListView:
    """按簇规模倒序的观点聚类，含各组代表性言论（FR-47）。"""
    return ClusterListView(**await service.list_clusters(repo, content_repo, session,
                                                        event_id))


@router.get("/{event_id}/clusters/{cluster_id}/members",
            response_model=PageResult[ContentItem], summary="观点簇成员下钻")
async def get_cluster_members(
    event_id: str, cluster_id: str, _actor: _ReadEvent,
    repo=Depends(get_event_repository),
    content_repo=Depends(get_content_repository),
    session: AsyncSession = Depends(get_db_session),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[ContentItem]:
    """下钻到簇内成员内容（FR-47 的「点击簇可下钻至成员内容」）。

    返回结构与舆情动态流一致，前端可复用同一张卡片与详情跳转。
    """
    total, items = await service.list_cluster_members(
        repo, content_repo, session, cluster_id, page=page, page_size=page_size)
    return paginate(total, [ContentItem(**item) for item in items], page, page_size)


@router.get("/{event_id}/graph", response_model=GraphView, summary="实体关系图谱")
async def get_graph(event_id: str, _actor: _ReadEvent,
                    repo=Depends(get_event_repository)) -> GraphView:
    """事件涉及的机构/人物/产品/事件及关联（FR-46）。

    只返回权重最高的若干条关系及其两端实体——全量返回会让前端图谱变成一团毛线。
    """
    event = await repo.get_event(event_id)
    if event is None:
        from app.errors import not_found

        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    graph = await repo.entity_graph(event_id, limit=service.GRAPH_RELATION_LIMIT)
    _, relation_count = await repo.entity_counts(event_id)
    return GraphView(event_id=event_id, nodes=graph.get("nodes", []),
                     edges=graph.get("edges", []),
                     truncated=relation_count > service.GRAPH_RELATION_LIMIT)
