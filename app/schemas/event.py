"""事件专题、传播溯源、观点聚类、实体图谱的响应模型（FR-45~FR-48）。

**事件级统计是读取时聚合出来的**：`event` 表里的 `total_volume` / `negative_ratio` /
`first_seen_at` / `last_seen_at` / `risk_level` 目前都是空值（T-11 建档时没算），
而它们都能由事件关联内容的标签与发布时间推出来。与其维护一份会与实际内容漂移的
缓存，这里一律**现算**（演示规模下每次查询几十毫秒），并把口径写在响应字段说明里。
"""

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from app.schemas.content import AuthorView, ContentItem, EngagementView
from engines.contracts.enums import ContentRole, EventStatus, RiskLevel, Sentiment, SourceType

__all__ = [
    "EventQuery",
    "EventStatsView",
    "EventSummary",
    "EventDetail",
    "RiskOverrideRequest",
    "TimelineNode",
    "TimelineView",
    "ClusterView",
    "ClusterListView",
    "ClusterMemberQuery",
    "GraphNode",
    "GraphEdge",
    "GraphView",
]


class EventQuery(PageQuery):
    """事件列表筛选。"""

    topic_id: str | None = Field(default=None, max_length=64)
    risk_level: RiskLevel | None = None
    status: EventStatus | None = None
    keyword: str | None = Field(default=None, max_length=64, description="按事件标题模糊匹配")


class EventStatsView(BaseModel):
    """事件统计。全部由关联内容的标签与发布时间聚合而来。"""

    content_count: int = Field(default=0, description="关联内容条数（含评论）")
    post_count: int = Field(default=0, description="其中内容主体（非评论）条数")
    total_volume: int = Field(default=0, description="总互动量（转评赞等之和）")
    negative_count: int = 0
    negative_ratio: float | None = Field(default=None, description="负面率 0~1")
    risk_level: RiskLevel | None = Field(default=None, description="关联内容中的最高风险等级")
    intensity_index: float | None = Field(default=None, description="平均烈度指数")
    first_seen_at: int | None = Field(default=None, description="最早发布时间")
    last_seen_at: int | None = Field(default=None, description="最近发布时间")
    source_distribution: dict[str, int] = Field(default_factory=dict, description="按信源的内容分布")
    sentiment_distribution: dict[str, int] = Field(default_factory=dict)


class EventSummary(BaseModel):
    """事件专题条目。

    `risk_level` 是**生效值**（人工复核过就是人工值），`algorithm_risk_level` 是
    算法按内容算出来的原值——FR-24 要求两者同时可查。
    """

    event_id: str
    title: str
    status: EventStatus
    topic_id: str | None = None
    topic_name: str = Field(default="", description="归属主题名称（按其内容的归属统计）")
    cluster_count: int = 0
    risk_level: RiskLevel | None = Field(default=None, description="生效风险等级")
    algorithm_risk_level: RiskLevel | None = Field(default=None, description="算法原始结论")
    is_manual_override: bool = Field(default=False, description="是否被人工作过复核")
    override_reason: str | None = Field(default=None, description="人工复核理由")
    stats: EventStatsView


class EventDetail(EventSummary):
    """事件详情：在条目之上补图谱规模与内容入口。"""

    entity_count: int = 0
    relation_count: int = 0


class RiskOverrideRequest(BaseModel):
    """风险等级人工复核（FR-24）。

    `reason` 是必填且**有最短长度**：理由是这个功能的核心产物——
    没有理由的等级变更无法复盘，等于把结论变成了意见。
    """

    risk_level: RiskLevel = Field(description="人工判定的风险等级")
    reason: str = Field(min_length=5, max_length=500, description="复核理由（至少 5 字）")


class TimelineNode(BaseModel):
    """传播溯源节点（FR-45）。

    `role` 取值见 `contracts.ContentRole`（与 `spread_node.node_role` 的 DDL 注释一致）：
    origin 首发 / ignition 引爆 / official 官方回应 / followup 媒体报道与跟进 / spread 扩散。
    """

    content_uid: str
    published_at: int
    role: ContentRole
    source_type: SourceType
    platform_label: str
    record_type: str = "post"
    author: AuthorView
    title: str = ""
    text: str = ""
    text_truncated: bool = False
    url: str = ""
    engagement: EngagementView
    sentiment: Sentiment | None = Field(
        default=None, description="未打标的内容为 null——时间线是传播结构，不因缺标签丢节点")
    risk_level: RiskLevel | None = None
    is_key_node: bool = Field(default=False, description="是否关键节点（首发/引爆/回应/跟进或高权重）")


class TimelineView(BaseModel):
    """传播溯源时间线。"""

    event_id: str
    node_scope: str = Field(description="key（仅关键节点）或 all（全部内容）")
    ordered_by: str = Field(default="published_at", description="排序依据：发布时间正序")
    role_source: str = Field(
        default="derived",
        description="节点角色的来源：derived 表示由内容与账号权重推导（非人工标注）")
    total_contents: int = Field(default=0, description="事件关联内容总数（含评论）")
    returned: int = Field(default=0, description="本次返回的节点数")
    role_summary: dict[str, int] = Field(default_factory=dict, description="各角色的节点数")
    nodes: list[TimelineNode] = Field(default_factory=list)


class ClusterView(BaseModel):
    """观点簇（FR-47）。"""

    cluster_id: str
    label: str
    summary: str = ""
    size: int = 0
    sentiment: Sentiment | None = None
    intent: str | None = None
    representatives: list[ContentItem] = Field(
        default_factory=list, description="代表性言论（落库时标记的成员）")


class ClusterListView(BaseModel):
    cluster_id_scope: str = "event"
    event_id: str
    total: int = 0
    items: list[ClusterView] = Field(default_factory=list)


class ClusterMemberQuery(PageQuery):
    """簇成员下钻的分页参数。"""


class GraphNode(BaseModel):
    id: str
    name: str
    type: str = Field(description="实体类型：机构/人物/产品/事件/地点")


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    weight: float = 0.0


class GraphView(BaseModel):
    """实体关系图谱（FR-46）。"""

    event_id: str
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    truncated: bool = Field(default=False, description="关系数超过上限时只返回权重最高的若干条")
