"""舆情内容查询的请求响应模型（FR-42~FR-44）。

列表与详情的字段差异是刻意的（设计规格 8.2）：列表**不返回长文本**
（正文截断 200 字并标记 `text_truncated`），详情才给全文 + OCR/ASR 证据，
避免一页 20 条内容把几百 KB 正文塞进响应。
"""

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from engines.contracts.enums import RiskLevel, Sentiment, SourceType

__all__ = [
    "ContentQuery",
    "EngagementView",
    "AuthorView",
    "ContentItem",
    "ContentDetail",
    "ModalityAssetView",
    "LIST_TEXT_LIMIT",
]

# 列表正文截断长度（设计规格 8.2）
LIST_TEXT_LIMIT = 200


class ContentQuery(PageQuery):
    """多维筛选（FR-42/43）。所有字段可选，组合即「与」。

    用枚举类型而非裸字符串：写错字面值（如 `negativee`）会被 schema 拦下返回 422，
    而不是静默筛出空结果——后者的表现是「这个筛选项没数据」，很容易被误判成数据缺口。
    """

    topic_id: str | None = Field(default=None, max_length=64, description="监测主题")
    source_type: SourceType | None = Field(default=None, description="信源类型")
    sentiment: Sentiment | None = Field(default=None, description="情感倾向")
    risk_level: RiskLevel | None = Field(default=None, description="风险等级")
    start_time: int | None = Field(default=None, description="发布时间起（Unix 时间戳）")
    end_time: int | None = Field(default=None, description="发布时间止（Unix 时间戳）")
    keyword: str | None = Field(default=None, max_length=64, description="正文关键词")
    is_suspected_bot: bool | None = Field(default=None, description="只要/排除疑似水军")
    is_manual_override: bool | None = Field(default=None, description="是否被人工复核过")


class EngagementView(BaseModel):
    """互动量。字段名与 `contracts/content.Engagement` 一致。"""

    likes: int = 0
    comments: int = 0
    shares: int = 0
    collects: int = 0
    replies: int = 0
    total: int = 0


class AuthorView(BaseModel):
    """发布者。粉丝量与影响力来自 `platform_account`。"""

    account_id: str = ""
    nickname: str = ""
    follower_count: int = 0
    verified_type: str = ""
    influence_score: float = 0.0
    is_kol: bool = False
    ip_location: str = ""


class ModalityAssetView(BaseModel):
    """多模态解析产物（FR-44 的 OCR / ASR 视图）。

    `parse_status=failed` 是正常结果之一（ADR-06：解析失败不阻断入库），
    因此详情页要能展示「失败及原因」，而不是把这条资产藏掉。
    """

    asset_id: str
    asset_type: str
    parser: str = ""
    parse_status: str
    parsed_text: str = ""
    error_message: str = ""
    origin_url: str = ""


class ContentItem(BaseModel):
    """列表条目：标签 + 原文摘要。"""

    content_uid: str
    source_type: SourceType
    platform_label: str
    record_type: str = "post"
    title: str = ""
    text: str = ""
    text_truncated: bool = False
    url: str = ""
    published_at: int = 0
    author: AuthorView
    engagement: EngagementView

    topic_id: str | None = None
    topic_name: str = ""

    sentiment: Sentiment
    emotion: str | None = None
    sentiment_confidence: float | None = None
    risk_level: RiskLevel | None = None
    intensity_index: float | None = None
    is_manual_override: bool = False
    is_suspected_bot: bool = False


class ContentDetail(ContentItem):
    """详情：全文 + 多模态证据 + 判定依据。"""

    raw_text: str = ""
    source_keyword: str = ""
    parent_content_uid: str = ""

    risk_factors: dict | None = Field(default=None, description="烈度/风险的各因子取值")
    override_reason: str | None = Field(default=None, description="人工复核理由")
    model_version: str = ""
    simhash: str = ""
    analyzed_at: int = 0

    modality_assets: list[ModalityAssetView] = Field(default_factory=list)
