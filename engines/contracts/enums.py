"""跨模块共享的枚举。

**取值不是随意定的**——它们必须与两个既有约定严格对齐，否则链路一跑就错位：

1. 数据生成器（`data/generator/scenarios.py`）写入数据库的字面值；
2. 数据库表（`sql/analysis/01_analysis_schema.sql`）注释里声明的取值范围。

改这里的值等于改数据契约，必须同步改数据生成器与 DDL，并重跑 `python -m engines.common.db.check`。
"""

from enum import Enum


class SourceType(str, Enum):
    """四类信源。值与原始表的来源分类、数据生成器的 SOURCE_* 常量一致。"""

    SOCIAL = "social_media"        # 社交媒体（微博）
    VIDEO = "short_video"          # 短视频（抖音）
    NEWS = "news_portal"           # 新闻门户
    FORUM = "industry_forum"       # 行业论坛

    @property
    def label(self) -> str:
        return _SOURCE_LABELS[self]


_SOURCE_LABELS = {
    SourceType.SOCIAL: "社交媒体",
    SourceType.VIDEO: "短视频",
    SourceType.NEWS: "新闻门户",
    SourceType.FORUM: "行业论坛",
}

# 信源 → 原始表名（表名不可推断，必须显式映射）
SOURCE_TABLES: dict[SourceType, dict[str, str]] = {
    SourceType.SOCIAL: {"content": "weibo_note", "comment": "weibo_note_comment"},
    SourceType.VIDEO: {"content": "douyin_aweme", "comment": "douyin_aweme_comment"},
    SourceType.NEWS: {"content": "news_article", "comment": "news_article_comment"},
    SourceType.FORUM: {"content": "forum_thread", "comment": "forum_post"},
}

# 原始表 → 内容表的业务主键字段名（评论表通过它关联内容，**不是主键 id**）
BUSINESS_ID_FIELD: dict[str, str] = {
    "weibo_note": "note_id",
    "douyin_aweme": "aweme_id",
    "news_article": "id",
    "forum_thread": "thread_id",
}

# 原始表 → 发布时间字段（论坛与新闻用 publish_time，社交与短视频用 create_time）
PUBLISH_TIME_FIELD: dict[str, str] = {
    "weibo_note": "create_time",
    "douyin_aweme": "create_time",
    "news_article": "publish_time",
    "forum_thread": "publish_time",
}


class Sentiment(str, Enum):
    """情感倾向三分类。"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"

    @property
    def label(self) -> str:
        return {"positive": "正向", "neutral": "中性", "negative": "负向"}[self.value]


class Emotion(str, Enum):
    """负向情绪的细分标签。正向与中性不细分。

    仅用于负向内容——把「支持」这类正向情绪也塞进同一枚举会让统计口径混乱。
    """

    ANGER = "愤怒"
    DISAPPOINTMENT = "失望"
    PANIC = "恐慌"
    DOUBT = "质疑"


class RiskLevel(str, Enum):
    """风险等级（PRD 附录 B.2）。"""

    S = "S"
    A = "A"
    B = "B"
    C = "C"

    @property
    def label(self) -> str:
        return {"S": "极高危", "A": "高危", "B": "中危", "C": "低危"}[self.value]

    @property
    def severity(self) -> int:
        """数值化严重度，便于排序与比较（S=4 最高）。"""
        return {"S": 4, "A": 3, "B": 2, "C": 1}[self.value]


class ContentRole(str, Enum):
    """内容在传播链路中的角色。

    与数据生成器的角色定义一致，用于传播溯源识别首发与引爆节点。
    """

    ORIGIN = "origin"          # 首发
    IGNITION = "ignition"      # 引爆（KOL / 高权重账号）
    SPREAD = "spread"          # 扩散
    ANALYSIS = "analysis"      # 深度分析（论坛长帖）
    FOLLOWUP = "followup"      # 媒体跟进
    OFFICIAL = "official"      # 官方回应
    AMBIENT = "ambient"        # 背景讨论（与事件弱相关）
    COMMENT = "comment"        # 评论（非内容主体）


class EvidenceStrength(str, Enum):
    """证据强度：该维度可用于支撑结论的材料充足程度。

    值为 none 时，分析角色应输出占位正文并标记缺失，**不得臆造结论**。
    """

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    NONE = "none"

    @property
    def label(self) -> str:
        return {"strong": "充分", "medium": "一般", "weak": "不足", "none": "缺失"}[self.value]

    @classmethod
    def from_count(cls, count: int, *, strong: int = 10, medium: int = 5,
                   weak: int = 1) -> "EvidenceStrength":
        """按证据条数分档（阈值与参考实现一致）。"""
        if count >= strong:
            return cls.STRONG
        if count >= medium:
            return cls.MEDIUM
        if count >= weak:
            return cls.WEAK
        return cls.NONE
