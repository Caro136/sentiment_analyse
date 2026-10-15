"""态势大盘路由（API-21，FR-36~FR-41）。

**一个接口给全五个指标**：大盘首屏要同时渲染趋势、分布、词云与指标卡，
拆成五个接口就是五次往返（首屏 < 3s 的目标下这很浪费），而且它们本就来自
同一批物化数据、同一时刻——分开取还会出现「趋势是新的、分布是旧的」。

告警滚动列表（FR-38）走 `/api/v1/alerts`：实时性优先，不在这里重复物化。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.dashboard import DashboardMetrics
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.content.repository import SqlContentRepository
from app.services.dashboard import service
from app.services.dashboard.repository import SqlDashboardRepository
from app.services.event.repository import SqlEventRepository
from engines.common.db.session import get_db_session
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

_ReadDashboard = Annotated[UserRecord, Depends(require_permission(Permission.DASHBOARD_READ))]


async def get_dashboard_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlDashboardRepository:
    return SqlDashboardRepository(session)


@router.get("/metrics", response_model=DashboardMetrics, summary="大盘指标聚合")
async def get_metrics(
    _actor: _ReadDashboard,
    repo: SqlDashboardRepository = Depends(get_dashboard_repository),
    session: AsyncSession = Depends(get_db_session),
    topic_id: str | None = Query(default=None, max_length=64,
                                 description="按主题过滤，留空即全局"),
    hours: int | None = Query(default=None, ge=1, le=720,
                              description="取数窗口小时数（默认 7 天）"),
) -> DashboardMetrics:
    """趋势 / 情感分布 / 信源分布 / 热词 / 核心指标（FR-36~FR-40）。

    数据来自物化表（每 60 秒刷新一轮，`computed_at` 可核对）；
    **缺失或过期时会同步补算一次**再返回，因此不会出现「刚启动时大盘空图」。
    前端按 `computed_at` 与刷新周期做自动刷新（FR-41）。
    """
    view = await service.get_metrics(
        repo, event_repo=SqlEventRepository(session),
        content_repo=SqlContentRepository(session), session=session,
        topic_id=topic_id, hours=hours)
    return DashboardMetrics(**view)
