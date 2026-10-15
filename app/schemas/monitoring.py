"""监测配置的请求响应模型（FR-06~FR-13）。

命名与前端 `ConfigView` 的四个配置区对应：主题与词包、信源通道、运行配置。
预警规则与通知渠道属于 T-24，不在本模块内。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import PageQuery
from engines.contracts.enums import KeywordGroup, SourceType, TopicStatus

__all__ = [
    "KeywordItem",
    "TopicKeywordReplaceRequest",
    "TopicChannelReplaceRequest",
    "TopicCreateRequest",
    "TopicUpdateRequest",
    "TopicQuery",
    "TopicSummary",
    "TopicDetail",
    "ChannelItem",
    "ChannelUpdateRequest",
    "ChannelCheckItem",
    "ConfigItem",
    "ConfigView",
    "ConfigUpdateRequest",
]

MAX_TOPIC_NAME = 128
MAX_WORDS_PER_TOPIC = 200
MAX_KEYWORD_LENGTH = 128


class KeywordItem(BaseModel):
    """词包条目。

    同义词用**多条词条**表达（例如品牌组里同时放「星耀」与「星耀科技」）：
    `topic_keyword` 表没有同义词列，且同义词在召回上本就是「等价的多个关键词」。
    """

    word: str = Field(min_length=1, max_length=MAX_KEYWORD_LENGTH)
    group_type: KeywordGroup
    weight: float = Field(default=1.0, ge=0.0, le=9.99,
                          description="词权重，用于命中排序（0.1~9.99）")


class TopicKeywordReplaceRequest(BaseModel):
    """全量替换词包。

    用全量替换而非逐条增删：词包是「一份配置」，前端编辑完整体提交，
    逐条增删会引入「部分成功」的中间态（提交到一半失败，词包既不是旧的也不是新的）。
    """

    keywords: list[KeywordItem] = Field(default_factory=list,
                                        max_length=MAX_WORDS_PER_TOPIC)


class TopicChannelReplaceRequest(BaseModel):
    """全量替换主题启用的信源通道（FR-09）。"""

    channel_ids: list[str] = Field(default_factory=list, max_length=16)


class TopicCreateRequest(BaseModel):
    """新建监控主题（FR-06/07）。"""

    name: str = Field(min_length=1, max_length=MAX_TOPIC_NAME)
    description: str = Field(default="", max_length=1000)
    source_keyword: str | None = Field(
        default=None, max_length=128,
        description="对应原始数据 source_keyword 的取值；留空时从词包自动推导")
    owner_id: str | None = Field(
        default=None, max_length=64,
        description="负责人；留空即当前用户。仅管理员可指定他人")
    start_at: int | None = Field(default=None, description="生效时间戳，留空即立即生效")
    status: TopicStatus = TopicStatus.ENABLED
    keywords: list[KeywordItem] = Field(default_factory=list,
                                       max_length=MAX_WORDS_PER_TOPIC)
    channel_ids: list[str] = Field(default_factory=list, max_length=16)


class TopicUpdateRequest(BaseModel):
    """更新主题元信息。省略的字段不改；**词包不在此接口**（见 keywords 端点）。"""

    name: str | None = Field(default=None, min_length=1, max_length=MAX_TOPIC_NAME)
    description: str | None = Field(default=None, max_length=1000)
    source_keyword: str | None = Field(default=None, max_length=128)
    start_at: int | None = None
    status: TopicStatus | None = None


class TopicQuery(PageQuery):
    """主题列表筛选。"""

    status: TopicStatus | None = None
    owner_id: str | None = None
    keyword: str | None = Field(default=None, max_length=64,
                                description="按主题名称模糊匹配")


class KeywordGroupStat(BaseModel):
    """词包分组统计（列表页展示，避免把几百个词条塞进列表响应）。"""

    group_type: KeywordGroup
    label: str
    count: int


class TopicSummary(BaseModel):
    """主题摘要（列表用）。"""

    topic_id: str
    name: str
    description: str = ""
    source_keyword: str | None = None
    owner_id: str
    status: TopicStatus
    start_at: int | None = None
    created_at: int
    keyword_count: int = 0
    keyword_groups: list[KeywordGroupStat] = Field(default_factory=list)


class TopicDetail(TopicSummary):
    """主题详情：带全量词包与关联通道。"""

    model_config = ConfigDict(from_attributes=True)

    keywords: list[KeywordItem] = Field(default_factory=list)
    channel_ids: list[str] = Field(default_factory=list)


class ChannelItem(BaseModel):
    """信源通道（FR-11/12）。"""

    channel_id: str
    channel_type: SourceType
    type_label: str
    name: str
    enabled: bool
    poll_interval_min: int
    lookback_hours: int
    last_collected_at: int | None = None
    last_collected_count: int | None = None
    updated_at: int


class ChannelUpdateRequest(BaseModel):
    """启停通道 / 调整采集频率与窗口（FR-11/12）。省略的字段不改。"""

    enabled: bool | None = None
    poll_interval_min: int | None = Field(default=None, ge=1, le=1440,
                                          description="轮询间隔（分钟）")
    lookback_hours: int | None = Field(default=None, ge=1, le=8760,
                                       description="回溯时间窗（小时）")


class ChannelCheckItem(BaseModel):
    """通道连通性自检结果（FR-13）。

    `db_reachable=False` 表示库本身连不上（此时其余字段无意义）；
    库可达但 `content_rows=0` 表示通道对应的原始表里没有数据——这才是
    「通道不可用」的真实表现，比一句「连通正常」有用得多。
    """

    channel_id: str
    channel_type: SourceType
    type_label: str
    name: str
    enabled: bool
    db_reachable: bool
    content_table: str = ""
    comment_table: str = ""
    content_rows: int = 0
    comment_rows: int = 0
    latest_published_at: int | None = None
    last_collected_at: int | None = None
    last_collected_count: int | None = None
    message: str = ""


class ConfigItem(BaseModel):
    """一个可热更新的运行配置项。"""

    key: str
    value: Any
    value_type: str
    label: str
    description: str = ""
    overridden: bool = Field(
        default=False, description="是否被运行时热更新覆盖（未覆盖时取自 .env）")


class ConfigView(BaseModel):
    """运行配置视图（白名单内的键，不含任何密钥）。"""

    items: list[ConfigItem]
    overridden_keys: list[str] = Field(default_factory=list)


class ConfigUpdateRequest(BaseModel):
    """运行配置热更新。

    载荷是扁平的 `{配置键: 新值}`，键必须在白名单内——不在白名单里的键
    **整批拒绝**（错误里列出可用键），而不是静默忽略：静默忽略会让管理员
    以为改生效了。
    """

    updates: dict[str, Any] = Field(min_length=1, description="键值对，键为环境变量名")
