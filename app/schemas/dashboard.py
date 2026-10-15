"""态势大盘的响应模型（FR-36~FR-41）。"""

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "OverviewMetrics",
    "TrendPoint",
    "VolumeTrend",
    "SentimentDistribution",
    "SourceDistributionItem",
    "SourceDistribution",
    "HotWord",
    "HotWords",
    "DashboardMetrics",
]


class OverviewMetrics(BaseModel):
    """核心指标卡片（FR-40）。"""

    total_volume: int = Field(default=0, description="窗口内总声量（条数）")
    negative_count: int = 0
    negative_ratio: float | None = Field(default=None, description="负面率 0~1，无数据为 null")
    high_risk_content_count: int = Field(default=0, description="S/A 级内容条数")
    source_count: int = Field(default=0, description="覆盖的信源数")
    open_alert_count: int = Field(default=0, description="未关闭告警数")
    high_level_alert_count: int = Field(default=0, description="其中 S/A 级告警数")
    high_risk_event_count: int | None = Field(
        default=None, description="S/A 级事件数（按事件生效风险，与事件接口同口径）")
    open_ticket_count: int = Field(default=0, description="待处置工单数")
    first_analyzed_at: int | None = None
    last_analyzed_at: int | None = None


class TrendPoint(BaseModel):
    bucket_start: int = Field(description="桶起点时间戳")
    total: int = 0
    negative: int = 0


class VolumeTrend(BaseModel):
    """声量趋势（FR-36）。空桶补 0，避免折线把「没数据」抹平。"""

    bucket: str = Field(default="hour", description="粒度：hour/day")
    bucket_seconds: int = 3600
    points: list[TrendPoint] = Field(default_factory=list)


class SentimentDistribution(BaseModel):
    """情感分布（FR-37）。`counts` 的键为 positive/neutral/negative。"""

    total: int = 0
    counts: dict[str, int] = Field(default_factory=dict)
    ratios: dict[str, float] = Field(default_factory=dict)


class SourceDistributionItem(BaseModel):
    source_type: str
    label: str
    count: int = 0
    ratio: float = 0.0


class SourceDistribution(BaseModel):
    total: int = 0
    items: list[SourceDistributionItem] = Field(default_factory=list)


class HotWord(BaseModel):
    """热词（FR-39）。`kind` 标明来源：entity 实体 / keyword 词包词。"""

    word: str
    weight: float = 0.0
    kind: str = "entity"


class HotWords(BaseModel):
    total_terms: int = 0
    items: list[HotWord] = Field(default_factory=list)


class DashboardMetrics(BaseModel):
    """大盘指标聚合响应。

    `computed_at` 是物化时间——前端据此显示「数据更新于 X」，
    并可与 `REFRESH_INTERVAL_SECONDS`（60 秒）配合做自动刷新（FR-41）。
    """

    scope: str = Field(default="all", description="all 为全局，否则是主题业务ID")
    topic_id: str | None = None
    computed_at: int = 0
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="含 overview / volume_trend / sentiment_distribution "
                    "/ source_distribution / hot_words 五个键")
