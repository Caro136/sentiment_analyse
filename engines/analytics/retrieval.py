"""混合检索：向量召回 + 关键词召回，用 RRF 融合。

**为什么用 RRF（倒数排名融合）而不是加权求和**：
向量相似度是 0~1 的余弦值，关键词命中数是 0~N 的整数——量纲完全不同，
直接加权必须先归一化，而归一化又会引入"该用什么分布归一"的新问题。
RRF 只用**排名**：`score(d) = Σ 1/(k + rank_i(d))`，
天然免疫量纲差异，也不需要调权重。

本模块提供两样东西：
  1. `reciprocal_rank_fusion` —— 融合算法本身（纯函数，可单测）
  2. `vector_search` —— 向量召回的便捷封装

关键词召回的 SQL 逻辑不在这里：它由调用方（研判编排层）提供，
因为只有它掌握主题、时间窗与信源过滤条件。
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from engines.analytics.embedding import Embedder
from engines.analytics.vector_store import EvidenceVectorStore, SearchHit
from engines.common.logging import logger

# RRF 的平滑常数。取 60 是论文与原实现的经验值：
# 它决定了排名靠前与靠后的分数差距有多陡，太小会让头部独占，太大则区分度不足。
DEFAULT_RRF_K = 60

# 各召回通道的名字（与 contracts/evidence.py 的常量对应）
CHANNEL_KEYWORD = "keyword_recall"
CHANNEL_VECTOR = "semantic_recall"


@dataclass
class RetrievalHit:
    """一条融合后的检索结果。"""

    content_uid: str
    score: float
    channels: tuple[str, ...] = ()
    ranks: dict[str, int] = field(default_factory=dict)
    content: str = ""
    source_type: str = ""

    @property
    def channel_count(self) -> int:
        """被多少个通道命中。多通道同时命中通常意味着更强的相关性。"""
        return len(self.channels)


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[str]],
    *,
    k: int = DEFAULT_RRF_K,
) -> list[tuple[str, float, dict[str, int]]]:
    """倒数排名融合。

    `rankings` 是 {通道名: [内容标识按相关性降序]}。
    返回 [(标识, 融合分, {通道: 排名})]，按融合分降序。

    >>> reciprocal_rank_fusion({"a": ["x", "y"], "b": ["y", "z"]})[0][0]
    'y'
    """
    scores: dict[str, float] = defaultdict(float)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)

    for channel, uids in rankings.items():
        for rank, uid in enumerate(uids, start=1):
            scores[uid] += 1.0 / (k + rank)
            ranks[uid][channel] = rank

    ordered = sorted(scores.items(), key=lambda item: -item[1])
    return [(uid, round(score, 6), ranks[uid]) for uid, score in ordered]


def fuse_hits(
    keyword_uids: Sequence[str],
    vector_hits: Sequence[SearchHit],
    *,
    limit: int = 20,
    k: int = DEFAULT_RRF_K,
) -> list[RetrievalHit]:
    """把关键词结果与向量结果融合成一个有序列表。

    向量结果里带着正文与信源，融合后一并保留——
    上层（章节生成）需要这些字段来渲染证据块，再查一次库是浪费。
    """
    rankings = {
        CHANNEL_KEYWORD: list(keyword_uids),
        CHANNEL_VECTOR: [hit.content_uid for hit in vector_hits],
    }
    if not keyword_uids:
        rankings.pop(CHANNEL_KEYWORD)
    if not vector_hits:
        rankings.pop(CHANNEL_VECTOR)

    if not rankings:
        return []

    payload = {hit.content_uid: hit for hit in vector_hits}
    fused = reciprocal_rank_fusion(rankings, k=k)

    results: list[RetrievalHit] = []
    for uid, score, ranks in fused[:limit]:
        hit = payload.get(uid)
        results.append(RetrievalHit(
            content_uid=uid,
            score=score,
            channels=tuple(sorted(ranks)),
            ranks=ranks,
            content=hit.content if hit else "",
            source_type=hit.source_type if hit else "",
        ))
    return results


async def vector_search(
    query: str,
    *,
    store: EvidenceVectorStore | None = None,
    embedder: Embedder | None = None,
    top_k: int = 30,
    expr: str | None = None,
) -> list[SearchHit]:
    """向量召回的便捷封装。编码或检索失败时返回空列表并记日志。

    返回空而不是抛异常：向量召回是**增强通道**，
    它挂了还有关键词通道兜底，不该让整条研判链路中断。
    """
    if not query.strip():
        return []

    encoder = embedder or Embedder.from_settings()
    vector_store = store or EvidenceVectorStore.from_settings()

    try:
        vector = await encoder.embed_one(query)
        if not vector:
            return []
        return vector_store.search(vector, top_k=top_k, expr=expr)
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量召回失败，已跳过该通道：{}: {}",
                       type(exc).__name__, str(exc)[:140])
        return []
