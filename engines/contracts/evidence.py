"""统一证据模型。

一条「证据」= 一条可被追溯、可被引用的原始内容。研究员的每个结论都必须能指回
支撑它的证据——这是 PRD FR-32「分析结果溯源」在数据层的落实，
也是本平台区别于「让大模型凭空写一份报告」的地方。

数据流：

    多路召回 → EvidencePool → 打分排序 → 语义聚类 → 按维度调拨
                                                    ↓
                                        SectionEvidencePack（渲染为 Prompt 证据块）
                                                    ↓
                                            章节正文（可追溯到每条证据）
"""

from dataclasses import dataclass, field

from engines.contracts.content import Engagement, hotness_score
from engines.contracts.enums import EvidenceStrength, SourceType

# ---------------------------------------------------------------------------
# 召回通道
# ---------------------------------------------------------------------------

CHANNEL_KEYWORD = "keyword_recall"      # 关键词命中（主题词 LIKE）
CHANNEL_COMMENT = "comment_recall"      # 评论召回（观点与情绪线索）
CHANNEL_HOT = "hot_recall"              # 热度召回（时间窗 + 热度分）
CHANNEL_SEMANTIC = "semantic_recall"    # 语义召回（Milvus 向量）

RETRIEVAL_CHANNELS = (CHANNEL_KEYWORD, CHANNEL_COMMENT, CHANNEL_HOT, CHANNEL_SEMANTIC)

CHANNEL_LABELS = {
    CHANNEL_KEYWORD: "关键词召回",
    CHANNEL_COMMENT: "评论召回",
    CHANNEL_HOT: "热度召回",
    CHANNEL_SEMANTIC: "语义召回",
}

# 通道权重：参与最终得分计算。语义召回权重最高——它命中「表达不同但语义相近」的内容，
# 正是关键词召回漏掉的那部分。
CHANNEL_WEIGHTS: dict[str, float] = {
    CHANNEL_SEMANTIC: 0.50,
    CHANNEL_KEYWORD: 0.40,
    CHANNEL_COMMENT: 0.40,
    CHANNEL_HOT: 0.20,
}

# 通道配额：排序后每个通道最多保留多少条。
# 配额的意义是保证「每个通道都有代表」——纯按分数截断会让高热度内容挤掉全部评论类证据，
# 而评论恰恰是情绪与观点分析的主要材料。
CHANNEL_QUOTAS: dict[str, int] = {
    CHANNEL_COMMENT: 20,
    CHANNEL_KEYWORD: 10,
    CHANNEL_SEMANTIC: 10,
    CHANNEL_HOT: 10,
}

MAX_EVIDENCE_RECORDS = 50      # 证据池上限，防止上下文溢出
RETRIEVAL_SCORE_WEIGHT = 0.60  # 最终得分中检索相关度的占比
HOTNESS_SCORE_WEIGHT = 0.40    # 最终得分中归一化热度的占比


# ---------------------------------------------------------------------------
# 检索元信息
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetrievalMeta:
    """一条证据的来源轨迹：它是怎么被找出来的。

    保留这个信息是为了让排序与溯源可解释——「这条证据为什么排在前面」
    应当能从命中通道与得分上看出来。
    """

    matched_queries: tuple[str, ...] = ()        # 命中的查询词
    retrieval_channels: tuple[str, ...] = ()     # 命中的召回通道
    retrieval_scores: dict[str, float] = field(default_factory=dict)  # 各通道得分

    @property
    def primary_channel(self) -> str:
        if not self.retrieval_scores:
            return self.retrieval_channels[0] if self.retrieval_channels else ""
        return max(self.retrieval_scores, key=lambda k: self.retrieval_scores[k])


# ---------------------------------------------------------------------------
# 证据记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceRecord:
    """一条证据。"""

    record_id: str
    content_uid: str
    source_type: SourceType
    source_table: str = ""
    title: str = ""
    content: str = ""
    author_nickname: str = ""
    published_at: int = 0
    url: str = ""
    source_keyword: str = ""
    record_type: str = "post"          # post 内容 / comment 评论
    engagement: Engagement = field(default_factory=Engagement)
    retrieval: RetrievalMeta = field(default_factory=RetrievalMeta)
    hotness_score: float = 0.0
    final_score: float = 0.0           # 排序得分，见 score_evidence
    cluster_id: str | None = None      # 所属聚类（调拨依据）

    @property
    def strength(self) -> EvidenceStrength:
        return EvidenceStrength.from_count(1)

    @property
    def display_text(self) -> str:
        return self.content or self.title

    @property
    def is_comment(self) -> bool:
        return self.record_type == "comment"

    def summary(self, limit: int = 120) -> str:
        """截断摘要，用于 Prompt 证据块。"""
        text = self.display_text.replace("\n", " ").strip()
        return text[:limit] + ("…" if len(text) > limit else "")


def score_evidence(record: EvidenceRecord, max_hotness: float) -> float:
    """计算证据最终得分（参考实现的二因子模型）。

        最终得分 = 检索相关度 × 0.60 + 归一化热度 × 0.40

    两个因子的权重差异是有意的：检索相关度决定「这条内容是否在讲这件事」，
    热度决定「它有多大影响」。相关性不足的高热度内容只是噪声。
    """
    channel = record.retrieval.primary_channel
    retrieval_score = record.retrieval.retrieval_scores.get(channel, 0.0)
    channel_weight = CHANNEL_WEIGHTS.get(channel, 0.3)

    normalized_hotness = (record.hotness_score / max_hotness) if max_hotness > 0 else 0.0
    score = (retrieval_score * channel_weight * RETRIEVAL_SCORE_WEIGHT
             + normalized_hotness * HOTNESS_SCORE_WEIGHT)
    return min(score, 1.0)


def build_engagement(**counts: int) -> Engagement:
    """便捷构造互动量并直接算出热度分。"""
    return Engagement(**counts)


def engagement_hotness(engagement: Engagement) -> float:
    """互动量 → 热度分（转发自 content 模块，便于证据层单点调用）。"""
    return hotness_score(engagement)


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceCluster:
    """语义聚类的一个簇。

    `dimension_key` 是簇被路由到的研究维度——调拨阶段直接按它取证据，
    不必再做一次相似度计算。
    """

    cluster_id: str
    label: str = ""
    summary: str = ""
    member_ids: tuple[str, ...] = ()
    representative_ids: tuple[str, ...] = ()
    size: int = 0
    dimension_key: str | None = None
    dominant_sentiment: str = ""

    @property
    def is_empty(self) -> bool:
        return self.size == 0


@dataclass
class EvidencePool:
    """一次研召回的全部证据。

    可变容器（与内容模型不同）：排序、去重、聚类都在原地修改它的记录列表。
    """

    query: str
    records: list[EvidenceRecord] = field(default_factory=list)
    clusters: list[EvidenceCluster] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.records)

    @property
    def total_engagement(self) -> int:
        return sum(r.engagement.total for r in self.records)

    def by_cluster(self, cluster_id: str) -> list[EvidenceRecord]:
        return [r for r in self.records if r.cluster_id == cluster_id]

    def by_dimension(self, dimension_key: str) -> list[EvidenceRecord]:
        """按研究维度取证据——调拨阶段的主要入口。

        聚类结果通过 `cluster_{dimension_key}` 的命名约定关联到维度。
        """
        target = f"cluster_{dimension_key}"
        return [r for r in self.records if r.cluster_id == target]

    def platform_distribution(self) -> dict[str, int]:
        """各信源的证据条数，供章节规划的「证据地图」使用。"""
        dist: dict[str, int] = {}
        for record in self.records:
            key = record.source_type.value if isinstance(record.source_type, SourceType) \
                else str(record.source_type)
            dist[key] = dist.get(key, 0) + 1
        return dist


# ---------------------------------------------------------------------------
# 章节证据包
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionEvidencePack:
    """某一章节可用的证据集合，渲染后作为章节生成的 Prompt 输入。

    `strength` 是**必须暴露给模型的信息**：证据不足时要求模型基于有限材料谨慎表述，
    而不是自信地编造。参考实现在这一点上很明确——证据强度为 missing 的章节
    直接输出占位正文，不生成任何结论。
    """

    section_key: str
    used_query: str = ""
    evidence_count: int = 0
    strength: EvidenceStrength = EvidenceStrength.NONE
    evidence_source_blocks: tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.evidence_count == 0 or self.strength is EvidenceStrength.NONE

    @property
    def is_sufficient(self) -> bool:
        return self.strength in (EvidenceStrength.STRONG, EvidenceStrength.MEDIUM)

    @classmethod
    def from_records(cls, section_key: str, query: str,
                     records: list[EvidenceRecord], *,
                     max_blocks: int = 30) -> "SectionEvidencePack":
        """从证据列表构建证据包。

        `max_blocks` 限制渲染条数——证据再多也不能无限塞进 Prompt，
        超出上下文的部分对模型是噪声而非信息。
        """
        selected = records[:max_blocks]
        blocks = tuple(_render_block(i, r) for i, r in enumerate(selected, start=1))
        return cls(
            section_key=section_key,
            used_query=query,
            evidence_count=len(records),
            strength=EvidenceStrength.from_count(len(records)),
            evidence_source_blocks=blocks,
        )


def _render_block(index: int, record: EvidenceRecord) -> str:
    """渲染单条证据为 Prompt 中的文本块。

    带上来源、时间与互动量：模型据此判断证据的分量
    （一条 80 万粉账号发布的内容与一条零互动评论，说服力不同）。
    """
    source_label = record.source_type.label if isinstance(record.source_type, SourceType) \
        else str(record.source_type)
    return (
        f"[{index}] 来源={source_label} 作者={record.author_nickname} "
        f"互动={record.engagement.total} 时间={record.published_at}\n"
        f"    {record.summary(200)}"
    )
