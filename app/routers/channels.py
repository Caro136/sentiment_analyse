"""信源通道路由（API-06/07，FR-11~FR-13）。

`/check` 必须声明在 `/{channel_id}` 之前：FastAPI 按声明顺序匹配，
否则 `GET /channels/check` 会被当成 channel_id="check" 的请求。
本文件目前只有 PUT 用到路径参数，顺序上暂时无冲突，但依赖声明顺序是隐性的——
先写 `/check` 并留这条注释，避免将来加 `GET /{channel_id}` 时踩坑。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.routers.topics import get_monitoring_repository
from app.schemas.monitoring import ChannelCheckItem, ChannelItem, ChannelUpdateRequest
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.monitoring import channels as service
from app.services.monitoring.repository import ChannelRecord, SqlMonitoringRepository
from engines.common.db.session import get_db_session
from engines.contracts.enums import SourceType
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/channels", tags=["channels"])

_WriteChannel = Annotated[UserRecord, Depends(require_permission(Permission.CHANNEL_WRITE))]
_ReadChannel = Annotated[UserRecord, Depends(require_permission(Permission.CHANNEL_READ))]


def _to_item(record: ChannelRecord) -> ChannelItem:
    source_type = SourceType(record.channel_type)
    return ChannelItem(
        channel_id=record.channel_id,
        channel_type=source_type,
        type_label=source_type.label,
        name=record.name,
        enabled=record.enabled,
        poll_interval_min=record.poll_interval_min,
        lookback_hours=record.lookback_hours,
        last_collected_at=record.last_collected_at,
        last_collected_count=record.last_collected_count,
        updated_at=record.updated_at,
    )


@router.get("", response_model=list[ChannelItem], summary="信源通道列表")
async def list_channels(_actor: _ReadChannel,
                        repo=Depends(get_monitoring_repository)) -> list[ChannelItem]:
    """四类信源通道的当前状态。

    集合固定为四类（与原始表一一对应），首次访问时按默认值补齐，
    因此全新环境也不会返回空列表。
    """
    return [_to_item(c) for c in await service.list_channels(repo)]


@router.get("/check", response_model=list[ChannelCheckItem], summary="通道连通性自检")
async def check_channels(
    _actor: _ReadChannel,
    repo=Depends(get_monitoring_repository),
    session: AsyncSession = Depends(get_db_session),
    channel_id: str | None = Query(default=None, max_length=64,
                                   description="只自检指定通道，留空即全部"),
) -> list[ChannelCheckItem]:
    """一键测试通道可用性（FR-13）。

    返回库可达性、原始表条数与最新发布时间——不探测外部站点（本项目无真实采集器，
    那种「连通正常」是编出来的）。`repo` 与 `session` 共用同一个请求级事务。
    """
    results = await service.check_channels(repo, session, channel_id=channel_id)
    return [ChannelCheckItem(**item) for item in results]


@router.put("/{channel_id}", response_model=ChannelItem, summary="启停通道 / 调整采集参数")
async def update_channel(channel_id: str, payload: ChannelUpdateRequest,
                         _actor: _WriteChannel,
                         repo=Depends(get_monitoring_repository)) -> ChannelItem:
    """启停通道、调整轮询间隔与回溯窗口（FR-11/12）。停用不影响已入库的历史数据。"""
    updated = await service.update_channel(
        repo, channel_id=channel_id, enabled=payload.enabled,
        poll_interval_min=payload.poll_interval_min,
        lookback_hours=payload.lookback_hours)
    return _to_item(updated)
