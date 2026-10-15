"""预警路由（API-19/20/30，FR-52~FR-56）。

权限三分：
  - 查看告警：`alert:read`（三类角色共有——大盘、工作台、运维都要看）
  - 认领 / 关闭：`alert:ack`（分析师与公关；管理员只运维不处置）
  - 规则维护与补扫：`alert_rule:write`（仅管理员）

`/scan` 用 POST：它是**动作**而非资源读取（会写告警记录），
且必须与事件驱动路径区分开——它只用于存量补扫与演示。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.alert import (
    AlertQuery,
    AlertRuleCreateRequest,
    AlertRuleUpdateRequest,
    AlertRuleView,
    AlertScanView,
    AlertStatusUpdateRequest,
    AlertView,
)
from app.routers.system import get_task_repository
from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.security import require_permission
from app.services.alert import service
from app.services.auth.repository import UserRecord
from engines.common.db.session import get_db_session
from engines.contracts.enums import AlertStatus, RiskLevel
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

_ReadAlert = Annotated[UserRecord, Depends(require_permission(Permission.ALERT_READ))]
_AckAlert = Annotated[UserRecord, Depends(require_permission(Permission.ALERT_ACK))]
_ManageRule = Annotated[UserRecord, Depends(require_permission(Permission.ALERT_RULE_WRITE))]


@router.get("", response_model=PageResult[AlertView], summary="告警列表")
async def list_alerts(
    _actor: _ReadAlert,
    session: AsyncSession = Depends(get_db_session),
    status: AlertStatus | None = Query(default=None, description="按状态筛选"),
    level: RiskLevel | None = Query(default=None, description="按等级筛选"),
    event_id: str | None = Query(default=None, max_length=64, description="按事件筛选"),
    start_time: int | None = Query(default=None, description="触发时间起"),
    end_time: int | None = Query(default=None, description="触发时间止"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[AlertView]:
    """告警列表，按触发时间倒序（FR-56）。

    `response_seconds` 是响应时长，**未认领时为 null**——把「没人响应」算成 0
    会让这个指标永远好看。
    """
    query = AlertQuery(page=page, page_size=page_size, status=status, level=level,
                       event_id=event_id, start_time=start_time, end_time=end_time)
    total, items = await service.list_alerts(
        session, page=query.page, page_size=query.page_size,
        status=query.status.value if query.status else None,
        level=query.level.value if query.level else None,
        event_id=query.event_id, start_time=query.start_time, end_time=query.end_time)
    return paginate(total, [AlertView(**item) for item in items], query.page, query.page_size)


@router.put("/{alert_id}", response_model=AlertView, summary="告警认领 / 关闭")
async def update_alert(alert_id: str, payload: AlertStatusUpdateRequest,
                       actor: _AckAlert,
                       session: AsyncSession = Depends(get_db_session)) -> AlertView:
    """流转告警状态：未读 → 已读 → 已认领 → 已关闭（FR-56）。

    非法迁移返回 409 `BIZ_203`（例如把已关闭的告警改回未读）；**首次认领**写入
    `responded_at`，后续流转不覆盖它，保证响应时长指标不被刷新。
    """
    item = await service.update_status(session, alert_id=alert_id,
                                       status=payload.status.value, note=payload.note)
    return AlertView(**item)


@router.get("/rules", response_model=list[AlertRuleView], summary="预警规则列表")
async def list_rules(_actor: _ManageRule,
                     session: AsyncSession = Depends(get_db_session)) -> list[AlertRuleView]:
    return [AlertRuleView(**item) for item in await service.list_rules(session)]


@router.post("/rules", response_model=AlertRuleView, status_code=201, summary="新建预警规则")
async def create_rule(payload: AlertRuleCreateRequest, _actor: _ManageRule,
                      session: AsyncSession = Depends(get_db_session)) -> AlertRuleView:
    """新建规则（FR-53，仅管理员）。未给阈值时按规则类型的默认值补齐。"""
    item = await service.create_rule(
        session, rule_type=payload.rule_type.value, level=payload.level.value,
        threshold=payload.threshold, sensitive_words=payload.sensitive_words,
        enabled=payload.enabled)
    return AlertRuleView(**item)


@router.put("/rules/{rule_id}", response_model=AlertRuleView, summary="更新预警规则")
async def update_rule(rule_id: str, payload: AlertRuleUpdateRequest, _actor: _ManageRule,
                      session: AsyncSession = Depends(get_db_session)) -> AlertRuleView:
    """调整阈值 / 等级 / 敏感词库 / 启停（FR-53）。改动在下一次扫描即时生效，并留痕。"""
    item = await service.update_rule(
        session, rule_id=rule_id,
        level=payload.level.value if payload.level else None,
        threshold=payload.threshold, sensitive_words=payload.sensitive_words,
        enabled=payload.enabled)
    return AlertRuleView(**item)


@router.post("/scan", response_model=AlertScanView, summary="立即执行一次预警扫描")
async def scan_now(
    _actor: _ManageRule,
    session: AsyncSession = Depends(get_db_session),
    task_repo=Depends(get_task_repository),
    window_minutes: int = Query(default=60, ge=1, le=1440, description="扫描窗口（分钟）"),
    dedup_minutes: int = Query(default=30, ge=1, le=1440, description="收敛窗口（分钟）"),
    notify: bool = Query(default=True, description="是否下发通知（false 只落库）"),
) -> AlertScanView:
    """补扫入口：对存量数据补告警、演示与排障（FR-52）。

    正常链路是**事件驱动**的（打标完成即扫描），这里只是手动触发同一条链路；
    `notify=false` 可以在不打扰收件人的前提下看规则命中情况。
    扫描过程写 `task_run`，运维看板（FR-72）能看到它。
    """
    result = await service.scan_now(session, task_repo, window_minutes=window_minutes,
                                    dedup_minutes=dedup_minutes, notify=notify)
    return AlertScanView(**result)
