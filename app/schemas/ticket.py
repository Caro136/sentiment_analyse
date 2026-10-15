"""处置工单的请求响应模型（FR-49~FR-51）。"""

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from engines.contracts.enums import RiskLevel, TicketStatus

__all__ = [
    "TicketQuery",
    "TicketLogView",
    "TicketView",
    "TicketDetailView",
    "TicketCreateRequest",
    "TicketUpdateRequest",
    "ResponseDraftRequest",
    "DraftAdoptRequest",
    "ResponseDraftView",
]


class TicketQuery(PageQuery):
    """工单列表筛选。"""

    status: TicketStatus | None = None
    event_id: str | None = Field(default=None, max_length=64)
    assignee_id: str | None = Field(default=None, max_length=64)


class TicketLogView(BaseModel):
    """一条处置轨迹（FR-50）。"""

    action: str
    action_label: str = ""
    from_status: str | None = None
    from_status_label: str = ""
    to_status: str | None = None
    to_status_label: str = ""
    operator_id: str = ""
    note: str = ""
    operated_at: int


class TicketView(BaseModel):
    """工单摘要（列表用）。"""

    ticket_id: str
    event_id: str
    alert_id: str | None = None
    status: TicketStatus
    status_label: str = ""
    assignee_id: str | None = None
    risk_level: RiskLevel | None = None
    has_response_draft: bool = False
    created_at: int
    closed_at: int | None = None
    is_closed: bool = False
    allowed_transitions: list[str] = Field(
        default_factory=list, description="当前状态下允许流转到的目标状态")


class TicketDetailView(TicketView):
    """工单详情：带草案全文与处置轨迹。"""

    response_draft: str = ""
    logs: list[TicketLogView] = Field(default_factory=list)


class TicketCreateRequest(BaseModel):
    """为事件开一张处置工单。"""

    event_id: str = Field(max_length=64, description="关联事件")
    alert_id: str | None = Field(default=None, max_length=64, description="来源告警")
    assignee_id: str | None = Field(default=None, max_length=64, description="指定承办人")
    risk_level: RiskLevel | None = Field(default=None, description="留空取事件当前风险等级")
    note: str = Field(default="", max_length=500)


class TicketUpdateRequest(BaseModel):
    """状态流转 / 认领 / 批注（FR-49/50）。

    只传 `note` 即追加一条批注（不动状态）；传 `status` 走状态机校验；
    关闭动作（`resolved` / `ignored`）**仅公关**可执行。
    """

    status: TicketStatus | None = None
    assignee_id: str | None = Field(default=None, max_length=64)
    risk_level: RiskLevel | None = None
    note: str = Field(default="", max_length=1000, description="处置说明 / 批注")


class ResponseDraftRequest(BaseModel):
    """生成公关回应草案（FR-51）。"""

    cluster_labels: list[str] = Field(
        default_factory=list, max_length=20,
        description="观点聚类标签（由调用方从事件聚类接口取，用于让草案聚焦争议焦点）")


class DraftAdoptRequest(BaseModel):
    """采纳草案。`content` 是人工编辑后的最终文本，省略则沿用已生成内容。"""

    content: str | None = Field(default=None, max_length=5000)
    note: str = Field(default="", max_length=500)


class ResponseDraftView(BaseModel):
    """草案生成结果（**未落库**，采纳后才写进工单）。"""

    event_id: str
    title: str
    risk_level: RiskLevel | None = None
    evidence: dict = Field(default_factory=dict, description="生成所用的事实材料")
    draft: dict = Field(default_factory=dict, description="三段式草案")
    rendered: str = Field(default="", description="拼成可直接编辑的文本")
