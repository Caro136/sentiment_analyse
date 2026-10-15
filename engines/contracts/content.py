"""跨信源统一内容模型。

**存在理由**：四类信源的表结构互不相同——微博正文叫 `content`、抖音叫 `desc`、
新闻有 `title`、论坛有楼层；互动量字段名两两不同（`comment_count` vs `comments_count`、
`like_count` vs `comment_like_count`）。接入层把这些差异**收敛到本模型**，
上层（分析、检索、展示）只面对统一字段，**不得再感知平台差异**。

⚠️ 原始表的字段命名差异是刻意保留的（数据集的 `source_keyword` 字段就是为此设计），
   不要试图"顺手统一"数据库表——那会让接入层的映射职责消失，平台差异扩散到全链路。
"""

import hashlib
from dataclasses import dataclass, field

from engines.contracts.enums import SourceType

# 内容记录类型
RECORD_POST = "post"          # 内容主体
RECORD_COMMENT = "comment"    # 评论


def make_content_uid(source_type: SourceType | str, source_table: str,
                     business_id: int) -> str:
    """构造跨信源统一标识。

    格式：`{source_type}:{source_table}:{business_id}`

    这是**全链路的内容主键**：分析标签、事件关联、观点簇成员、传播节点都引用它。
    构造规则必须唯一——各处手写字符串极易拼错（多一个空格就产生一条幽灵记录），
    因此统一走本函数。

    >>> make_content_uid(SourceType.VIDEO, "douyin_aweme", 7648947265968639247)
    'short_video:douyin_aweme:7648947265968639247'
    """
    value = source_type.value if isinstance(source_type, SourceType) else str(source_type)
    return f"{value}:{source_table}:{business_id}"


def parse_content_uid(content_uid: str) -> tuple[str, str, int]:
    """解析统一标识，返回 (source_type, source_table, business_id)。"""
    parts = content_uid.split(":")
    if len(parts) != 3:
        raise ValueError(f"非法的 content_uid：{content_uid!r}")
    source_type, source_table, business_id = parts
    return source_type, source_table, int(business_id)


@dataclass(frozen=True)
class Author:
    """发布者。粉丝量与影响力来自 `platform_account` 表（原始表本身不含这些字段）。"""

    account_id: str
    nickname: str
    follower_count: int = 0
    verified_type: str = "普通用户"
    influence_score: float = 0.0
    ip_location: str = ""

    # 高权重账号阈值：达到即视为 KOL，用于预警的「高权重账号发声」规则与传播引爆识别
    KOL_INFLUENCE_THRESHOLD = 60.0

    @property
    def is_kol(self) -> bool:
        return self.influence_score >= self.KOL_INFLUENCE_THRESHOLD

    @property
    def is_official(self) -> bool:
        return self.verified_type in ("官方", "政务", "媒体")


@dataclass(frozen=True)
class Engagement:
    """互动量。

    四平台的原始字段名互不相同（`liked_count` / `comment_count` / `comments_count` /
    `like_count` / `comment_like_count` / `shared_count` / `share_count` / `reply_count`…），
    接入层负责映射到这里。缺失的维度补 0，不补 None——下游要参与加权计算。
    """

    likes: int = 0
    comments: int = 0
    shares: int = 0
    collects: int = 0
    replies: int = 0

    @property
    def total(self) -> int:
        return self.likes + self.comments + self.shares + self.collects + self.replies

    def as_dict(self) -> dict[str, int]:
        return {
            "likes": self.likes, "comments": self.comments, "shares": self.shares,
            "collects": self.collects, "replies": self.replies,
        }


# 热度权重：分享 > 评论 > 回复 > 收藏 > 点赞。
# 依据是传播力的衰减——分享一次内容会触达一个新的人群，而点赞只表示个人态度。
HOTNESS_WEIGHTS: dict[str, float] = {
    "shares": 5.0,
    "comments": 4.0,
    "replies": 3.0,
    "collects": 2.0,
    "likes": 1.0,
}


def hotness_score(engagement: Engagement) -> float:
    """按权重计算热度分。用于热度通道排序与证据最终得分。"""
    return sum(getattr(engagement, name) * weight
               for name, weight in HOTNESS_WEIGHTS.items())


# 媒体资源类型。定义在 contracts 而非 modality 包内：
# 适配器（产出 MediaRef）与解析器（消费 MediaRef）都要用，放这里避免两处定义漂移。
ASSET_IMAGE = "image"
ASSET_VIDEO = "video"
ASSET_AUDIO = "audio"


@dataclass(frozen=True)
class MediaRef:
    """内容携带的媒体资源引用。

    为什么要在统一模型里保留它：接入层做字段映射时**不能把资源地址丢掉**——
    OCR 与 ASR 需要它作为输入（FR-16/17）。若映射后只剩文本，
    多模态解析就无从下手，原始表里的 `cover_url` / `video_download_url` 也就白采集了。

    `suffix` 用于构造稳定的资产 ID（同一资源重复解析时 ID 不变，便于幂等写入）。
    """

    asset_type: str          # image / video / audio
    url: str
    suffix: str = ""         # 如 "cover" / "video"

    @property
    def asset_key(self) -> str:
        """资产标识片段，形如 `cover`；缺省时按类型取。"""
        return self.suffix or self.asset_type


@dataclass(frozen=True)
class UnifiedContent:
    """跨信源统一的内容视图。接入层的产出，分析层的输入。

    不可变：内容一旦从原始表读出就不应被改写。分析结果另行承载
    （`content_analysis` 表 / `AnalysisLabel`），这与 ADR-03「原始数据只读、
    分析结果独立存储」是同一个原则。
    """

    content_uid: str
    source_type: SourceType
    source_table: str
    business_id: int
    record_type: str = RECORD_POST
    # 评论所属内容的统一标识（仅 record_type=comment 时有值）。
    # 原始表中评论通过 note_id / aweme_id / article_id / thread_id 指向父内容，
    # 接入层把它换算成统一的 content_uid，上层做观点聚类时按此归并到事件。
    parent_content_uid: str = ""

    author: Author = field(default_factory=lambda: Author(account_id="", nickname=""))
    title: str = ""
    text: str = ""                 # 清洗后的正文（分析用）
    raw_text: str = ""             # 未清洗原文（展示与溯源用）
    published_at: int = 0
    url: str = ""
    source_keyword: str = ""
    engagement: Engagement = field(default_factory=Engagement)

    # 内容携带的媒体资源（图片 / 视频 / 音频），用于驱动 OCR 与 ASR
    media_refs: tuple[MediaRef, ...] = ()

    # 多模态解析出的附加文本（OCR / ASR）。为空表示无多模态内容或解析失败——
    # 解析失败不阻断入库（ADR-06），因此这里不区分"无内容"与"解析失败"，
    # 具体状态查 modality_asset 表。
    modality_texts: tuple[str, ...] = ()

    @property
    def display_text(self) -> str:
        """展示用文本：正文为空时回落到标题。"""
        return self.text or self.title

    @property
    def analysis_text(self) -> str:
        """分析用文本：正文 + 多模态文本拼接。

        OCR/ASR 文本与正文一并参与情感与实体分析——图片里的文字往往正是
        最关键的信息（如现场照片中的标语、视频字幕）。
        """
        parts = [p for p in (self.title, self.text) if p]
        parts.extend(t for t in self.modality_texts if t)
        return "\n".join(parts)

    @property
    def content_hash(self) -> str:
        """内容指纹，用于精确去重。"""
        raw = f"{self.source_type.value}|{self.display_text}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnalysisLabel:
    """内容分析标签。由 analytics 层产出，供大盘、筛选、预警消费。

    与 `content_analysis` 表一一对应，是它的领域模型形式。
    """

    sentiment: str = "neutral"
    emotion: str = ""
    sentiment_confidence: float = 0.0
    intensity_index: float = 0.0
    risk_level: str = "C"
    risk_factors: dict = field(default_factory=dict)
    is_manual_override: bool = False
    override_reason: str = ""
    model_version: str = ""

    @property
    def is_negative(self) -> bool:
        return self.sentiment == "negative"


@dataclass(frozen=True)
class AnalyzedContent:
    """内容 + 标签的组合视图。

    工作台列表、大盘聚合、预警规则匹配都以它为输入——
    它们既要看到内容本身（展示、筛选），又要看到标签（排序、规则判定）。
    """

    content: UnifiedContent
    label: AnalysisLabel | None = None

    @property
    def content_uid(self) -> str:
        return self.content.content_uid

    @property
    def is_negative(self) -> bool:
        return bool(self.label and self.label.is_negative)
