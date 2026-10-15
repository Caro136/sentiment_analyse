"""预警相关的请求响应模型（FR-52~FR-56）。"""

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from engines.contracts.enums import AlertRuleType, AlertStatus, RiskLevel

__all__ = [
    "AlertQuery",
    "AlertView",
    "AlertScanView",
    "AlertStatusUpdateRequest",
    "AlertRuleView",
    "AlertRuleCreateRequest",
    "AlertRuleUpdateRequest",
]


class AlertQuery(PageQuery):
    """告警列表筛选（FR-56）。"""

    status: AlertStatus | None = None
    level: RiskLevel | None = None
    event_id: str | None = Field(default=None, max_length=64)
    start_time: int | None = Field(default=None, description="触发时间起（Unix 时间戳）")
    end_time: int | None = Field(default=None, description="触发时间止")


class AlertView(BaseModel):
    """一条告警。"""

    alert_id: str
    rule_id: str | None = None
    rule_type: str | None = Field(default=None, description="触发规则类型")
    rule_label: str = Field(default="", description="规则类型的中文名")
    event_id: str | None = None
    topic_id: str | None = Field(default=None, description="归属主题（取自触发时窗口内的高频主题）")
    level: RiskLevel
    status: AlertStatus
    status_label: str = ""
    triggered_at: int
    responded_at: int | None = None
    response_seconds: int | None = Field(
        default=None, description="响应时长（秒）。未认领为 null——不能把「没人响应」算成 0")
    merged_count: int = Field(default=1, description="收敛合并计数")
    reason: str = Field(default="", description="判定依据的自然语言说明")
    detail: dict[str, Any] = Field(default_factory=dict, description="触发明细")
    notify_status: str | None = Field(default=None, description="通知投递：sent/degraded/skipped")
    notify_error: str | None = None


class AlertScanView(BaseModel):
    """一次预警扫描的结果（补扫入口）。

    字段给全是为了回答「为什么没报警」——窗口内多少内容、命中几条、建了几条告警。
    """

    scanned_topics: int = 0
    window_contents: int = 0
    hits: int = 0
    created: list[str] = Field(default_factory=list)
    merged: list[str] = Field(default_factory=list)
    notified: int = 0
    degraded: int = 0
    skipped: int = 0
    summary: str = ""


class AlertStatusUpdateRequest(BaseModel):
    """告警状态流转（FR-56）。"""

    status: AlertStatus = Field(description="目标状态：read/claimed/closed")
    note: str = Field(default="", max_length=500, description="处置说明（可选，仅记录在响应与日志）")


class AlertRuleView(BaseModel):
    """预警规则（FR-53）。"""

    rule_id: str
    rule_type: AlertRuleType
    type_label: str = ""
    level: RiskLevel
    threshold: float | None = Field(
        default=None,
        description="阈值：激增规则是相对增幅（0.5=50%），敏感词与 KOL 规则是条数")
    sensitive_words: list[str] = Field(default_factory=list)
    enabled: bool = True
    updated_at: int = 0


class AlertRuleCreateRequest(BaseModel):
    """新建规则。"""

    rule_type: AlertRuleType
    level: RiskLevel
    threshold: float | None = Field(default=None, ge=0, le=1000)
    sensitive_words: list[str] = Field(default_factory=list, max_length=200)
    enabled: bool = True


class AlertRuleUpdateRequest(BaseModel):
    """更新规则（省略的字段不改）。**阈值变更会即时生效于下一次扫描。**"""

    level: RiskLevel | None = None
    threshold: float | None = Field(default=None, ge=0, le=1000)
    sensitive_words: list[str] | None = Field(default=None, max_length=200)
    enabled: bool | None = None
