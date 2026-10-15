"""预警规则与告警记录的读写（`alert_rule` / `alert_record`）。

三条约定：

1. **先落库再通知**（本模块只管落库）。邮件可能失败、可能重试三次才降级，
   而告警本身绝不能因为「通知发不出去」而丢失——它是后续处置的唯一凭据。
2. **收敛在库里判断**（`find_open_alert`）。同一规则 + 同一事件在收敛窗口内
   已有未关闭的告警时，合并计数而不是新建一条，避免刷屏。
3. **认领记录响应时长**：`responded_at` 只在**首次**认领时写入，
   以后的状态流转不覆盖它（否则「响应时长」会被后续操作不断刷新，指标失去意义）。
"""

import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select

from engines.common.db.models import AlertRecord, AlertRule

__all__ = [
    "RuleRecord",
    "AlertRecordSnapshot",
    "create_alert",
    "create_rule",
    "find_open_alert",
    "get_alert",
    "get_rule",
    "list_alerts",
    "list_rules",
    "merge_alert",
    "new_alert_id",
    "new_rule_id",
    "update_alert_status",
    "update_notify_result",
    "update_rule",
]


@dataclass(frozen=True)
class RuleRecord:
    rule_id: str
    rule_type: str
    level: str
    threshold: float | None
    sensitive_words: tuple[str, ...]
    enabled: bool
    updated_at: int


@dataclass(frozen=True)
class AlertRecordSnapshot:
    alert_id: str
    rule_id: str | None
    event_id: str | None
    level: str
    status: str
    triggered_at: int
    responded_at: int | None
    merged_count: int
    detail: dict[str, Any]
    notify_status: str | None
    notify_error: str | None

    @property
    def response_seconds(self) -> int | None:
        """响应时长（秒）。未认领时为 None——不能把「没人响应」算成 0。"""
        if self.responded_at is None:
            return None
        return max(0, self.responded_at - self.triggered_at)


def new_rule_id() -> str:
    import secrets

    return f"rule_{int(time.time())}{secrets.token_hex(3)}"


def new_alert_id() -> str:
    import secrets

    return f"alert_{int(time.time())}{secrets.token_hex(3)}"


def _to_rule(row: AlertRule) -> RuleRecord:
    words = tuple(w.strip() for w in (row.sensitive_words or "").split(",") if w.strip())
    return RuleRecord(rule_id=row.rule_id, rule_type=row.rule_type, level=row.level,
                      threshold=(float(row.threshold) if row.threshold is not None else None),
                      sensitive_words=words, enabled=bool(row.enabled),
                      updated_at=row.updated_at)


def _to_alert(row: AlertRecord) -> AlertRecordSnapshot:
    import json

    detail: dict[str, Any] = {}
    if row.detail:
        try:
            parsed = json.loads(row.detail)
            detail = parsed if isinstance(parsed, dict) else {"value": parsed}
        except (TypeError, ValueError):
            detail = {"raw": row.detail[:200]}
    return AlertRecordSnapshot(
        alert_id=row.alert_id, rule_id=row.rule_id, event_id=row.event_id,
        level=row.level, status=row.status, triggered_at=row.triggered_at,
        responded_at=row.responded_at, merged_count=int(row.merged_count or 1),
        detail=detail, notify_status=row.notify_status, notify_error=row.notify_error)


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------

async def list_rules(session, *, enabled_only: bool = False) -> list[RuleRecord]:
    stmt = select(AlertRule)
    if enabled_only:
        stmt = stmt.where(AlertRule.enabled.is_(True))
    rows = (await session.execute(stmt.order_by(AlertRule.id))).scalars().all()
    return [_to_rule(row) for row in rows]


async def get_rule(session, rule_id: str) -> RuleRecord | None:
    row = (await session.execute(
        select(AlertRule).where(AlertRule.rule_id == rule_id))).scalar_one_or_none()
    return _to_rule(row) if row else None


async def create_rule(session, *, rule_id: str, rule_type: str, level: str,
                      threshold: float | None, sensitive_words: str,
                      enabled: bool = True) -> RuleRecord:
    row = AlertRule(rule_id=rule_id, rule_type=rule_type, level=level,
                    threshold=threshold, sensitive_words=sensitive_words,
                    enabled=enabled, updated_at=int(time.time()))
    session.add(row)
    await session.flush()
    return _to_rule(row)


async def update_rule(session, rule_id: str, fields: dict) -> RuleRecord | None:
    row = (await session.execute(
        select(AlertRule).where(AlertRule.rule_id == rule_id))).scalar_one_or_none()
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = int(time.time())
    await session.flush()
    return _to_rule(row)


# ---------------------------------------------------------------------------
# 告警记录
# ---------------------------------------------------------------------------

async def find_open_alert(session, *, rule_id: str, event_id: str,
                          since: int) -> AlertRecordSnapshot | None:
    """找收敛窗口内、同一规则与事件下**尚未关闭**的告警。"""
    row = (await session.execute(
        select(AlertRecord)
        .where(AlertRecord.rule_id == rule_id,
               AlertRecord.event_id == event_id,
               AlertRecord.triggered_at >= since,
               AlertRecord.status != "closed")
        .order_by(AlertRecord.triggered_at.desc())
        .limit(1))).scalar_one_or_none()
    return _to_alert(row) if row else None


async def create_alert(session, *, alert_id: str, rule_id: str | None, event_id: str | None,
                       level: str, detail: str, status: str = "unread") -> AlertRecordSnapshot:
    row = AlertRecord(alert_id=alert_id, rule_id=rule_id, event_id=event_id,
                      level=level, status=status, triggered_at=int(time.time()),
                      merged_count=1, detail=detail)
    session.add(row)
    await session.flush()
    return _to_alert(row)


async def merge_alert(session, alert_id: str, *, level: str, detail: str,
                      ) -> AlertRecordSnapshot | None:
    """收敛：合并计数 +1，等级取更严重的一侧，明细更新为最近一次。

    **不更新 `triggered_at`**：告警的起始时间保持首次触发那一刻，
    这样「响应时长」衡量的始终是「从第一次响起到有人认领」。
    """
    row = (await session.execute(
        select(AlertRecord).where(AlertRecord.alert_id == alert_id))).scalar_one_or_none()
    if row is None:
        return None
    row.merged_count = int(row.merged_count or 1) + 1
    if _severity(level) > _severity(row.level):
        row.level = level
    row.detail = detail
    await session.flush()
    return _to_alert(row)


def _severity(level: str | None) -> int:
    from engines.contracts.enums import RiskLevel

    try:
        return RiskLevel(level or "").severity
    except ValueError:
        return 0


async def update_notify_result(session, alert_id: str, *, status: str,
                               error: str | None = None) -> None:
    row = (await session.execute(
        select(AlertRecord).where(AlertRecord.alert_id == alert_id))).scalar_one_or_none()
    if row is None:
        return
    row.notify_status = status
    row.notify_error = (error or "")[:500] or None
    await session.flush()


async def get_alert(session, alert_id: str) -> AlertRecordSnapshot | None:
    row = (await session.execute(
        select(AlertRecord).where(AlertRecord.alert_id == alert_id))).scalar_one_or_none()
    return _to_alert(row) if row else None


async def list_alerts(session, *, page: int, page_size: int, status: str | None = None,
                      level: str | None = None, event_id: str | None = None,
                      start_time: int | None = None, end_time: int | None = None,
                      ) -> tuple[int, list[AlertRecordSnapshot]]:
    conditions = []
    if status:
        conditions.append(AlertRecord.status == status)
    if level:
        conditions.append(AlertRecord.level == level)
    if event_id:
        conditions.append(AlertRecord.event_id == event_id)
    if start_time is not None:
        conditions.append(AlertRecord.triggered_at >= start_time)
    if end_time is not None:
        conditions.append(AlertRecord.triggered_at <= end_time)

    total_stmt = select(func.count()).select_from(AlertRecord)
    rows_stmt = (select(AlertRecord)
                 .order_by(AlertRecord.triggered_at.desc(), AlertRecord.id.desc()))
    for condition in conditions:
        total_stmt = total_stmt.where(condition)
        rows_stmt = rows_stmt.where(condition)

    total = int((await session.execute(total_stmt)).scalar() or 0)
    rows = (await session.execute(
        rows_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return total, [_to_alert(row) for row in rows]


async def update_alert_status(session, alert_id: str, *, status: str,
                              responded_at: int | None = None) -> AlertRecordSnapshot | None:
    row = (await session.execute(
        select(AlertRecord).where(AlertRecord.alert_id == alert_id))).scalar_one_or_none()
    if row is None:
        return None
    row.status = status
    # responded_at 只在首次认领时写入：后续流转不刷新它，否则响应时长指标失真
    if responded_at is not None and row.responded_at is None:
        row.responded_at = responded_at
    await session.flush()
    return _to_alert(row)


async def count_by_status(session) -> dict[str, int]:
    rows = (await session.execute(
        select(AlertRecord.status, func.count()).group_by(AlertRecord.status))).all()
    return {status: int(count) for status, count in rows}
