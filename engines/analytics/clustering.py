"""语义聚类与观点聚类（PRD FR-27 / FR-28）。

**为什么用语义向量而不是关键词**：同一条抱怨有无数种说法——
「发热严重」「后盖烫手」「可以当暖手宝」「散热不行」，关键词匹配一个都抓不住，
语义向量能把它们放得很近。舆情分析里"同一件事的不同表述"恰恰是常态。

流程：编码 → L2 归一化 → KMeans → 按质心距离排序取代表文本 → LLM 命名。

簇数不写死：按 `内容数 / 最小簇规模` 估算并设上下限。
固定簇数在内容量变化时会碎成噪声簇或挤成一个大簇。

**失败降级为单簇**而不是抛异常：聚类失败（如编码服务不可用）时，
后续的章节生成仍应能进行——把全部证据放一个维度里，总比整条链路中断好。
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from engines.analytics.embedding import Embedder
from engines.common.logging import logger

MIN_CLUSTER_SIZE = 3
MAX_CLUSTERS = 12
REPRESENTATIVE_COUNT = 3


@dataclass(frozen=True)
class ClusterInfo:
    """一个观点簇。"""

    cluster_id: str
    label: str
    size: int
    member_ids: tuple[str, ...]
    representative_ids: tuple[str, ...]
    dominant_sentiment: str = ""
    summary: str = ""


@dataclass
class ClusteringResult:
    """聚类产出。"""

    clusters: list[ClusterInfo] = field(default_factory=list)
    assignments: dict[str, str] = field(default_factory=dict)   # uid → cluster_id
    method: str = "kmeans"
    degraded_reason: str = ""

    def __len__(self) -> int:
        return len(self.clusters)

    @property
    def ok(self) -> bool:
        return bool(self.clusters)

    @property
    def largest_size(self) -> int:
        return max((c.size for c in self.clusters), default=0)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _normalize(matrix: np.ndarray) -> np.ndarray:
    """L2 归一化：归一化后向量内积即余弦相似度，省去每次算除法。"""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-8)


def _determine_k(count: int, min_size: int, max_k: int) -> int:
    """按样本量估算簇数。

    上下限都要有：太少会挤成一个大簇（失去区分度），
    太多会碎成一堆单条簇（标签没有概括力）。
    """
    if count < min_size * 2:
        return 1
    return max(2, min(max_k, count // min_size, count))


def _kmeans(matrix: np.ndarray, k: int) -> np.ndarray:
    from sklearn.cluster import KMeans

    model = KMeans(n_clusters=k, n_init="auto", random_state=42)
    return model.fit_predict(matrix)


def _dominant_sentiment(sentiments: dict[str, str] | None,
                        member_ids: Sequence[str]) -> str:
    if not sentiments:
        return ""
    counter = Counter(sentiments.get(uid, "") for uid in member_ids)
    counter.pop("", None)
    return counter.most_common(1)[0][0] if counter else ""


def _truncate_label(text: str, limit: int = 16) -> str:
    cleaned = (text or "").replace("\n", " ").strip()
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def _single_cluster(usable: list[tuple[str, str]],
                    sentiments: dict[str, str] | None,
                    reason: str, method: str = "degraded") -> ClusteringResult:
    """降级结果：全部内容归为一簇。"""
    uids = tuple(uid for uid, _ in usable)
    cluster = ClusterInfo(
        cluster_id="cluster_0",
        label=_truncate_label(usable[0][1]) if usable else "",
        size=len(uids),
        member_ids=uids,
        representative_ids=uids[:REPRESENTATIVE_COUNT],
        dominant_sentiment=_dominant_sentiment(sentiments, uids),
    )
    return ClusteringResult(clusters=[cluster],
                            assignments={uid: "cluster_0" for uid in uids},
                            method=method, degraded_reason=reason)


# ---------------------------------------------------------------------------
# 聚类主流程
# ---------------------------------------------------------------------------

async def cluster_texts(
    items: Sequence[tuple[str, str]],
    *,
    sentiments: dict[str, str] | None = None,
    embedder: Embedder | None = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    max_clusters: int = MAX_CLUSTERS,
    representatives: int = REPRESENTATIVE_COUNT,
) -> ClusteringResult:
    """对 (标识, 文本) 列表做语义聚类。

    返回的 `assignments` 是「内容 → 簇」的映射，供落库与证据调拨使用。
    """
    usable = [(uid, (text or "").strip()) for uid, text in items if (text or "").strip()]
    if not usable:
        return ClusteringResult(degraded_reason="没有可聚类的内容")

    if len(usable) < min_cluster_size * 2:
        return _single_cluster(usable, sentiments, "样本量不足，未做聚类", method="single")

    encoder = embedder or Embedder.from_settings()
    try:
        vectors = await encoder.embed([text for _, text in usable])
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量编码失败，聚类降级为单簇：{}", type(exc).__name__)
        return _single_cluster(usable, sentiments, f"编码失败：{type(exc).__name__}")

    matrix = _normalize(np.asarray(vectors, dtype=np.float32))
    k = _determine_k(len(usable), min_cluster_size, max_clusters)
    labels = _kmeans(matrix, k) if k > 1 else np.zeros(len(usable), dtype=int)

    clusters: list[ClusterInfo] = []
    assignments: dict[str, str] = {}

    for index in range(int(labels.max()) + 1):
        member_positions = np.where(labels == index)[0]
        if len(member_positions) == 0:
            continue

        member_vectors = matrix[member_positions]
        centroid = member_vectors.mean(axis=0)
        # 向量已归一化，与质心的内积即相似度；按相似度降序取代表性成员
        order = np.argsort(-(member_vectors @ centroid))
        ordered_positions = member_positions[order]

        member_ids = tuple(usable[i][0] for i in ordered_positions)
        cluster_id = f"cluster_{index}"
        for uid in member_ids:
            assignments[uid] = cluster_id

        clusters.append(ClusterInfo(
            cluster_id=cluster_id,
            label=_truncate_label(usable[ordered_positions[0]][1]),
            size=len(member_ids),
            member_ids=member_ids,
            representative_ids=member_ids[:representatives],
            dominant_sentiment=_dominant_sentiment(sentiments, member_ids),
        ))

    clusters.sort(key=lambda c: -c.size)
    logger.info("语义聚类完成：{} 条内容 → {} 簇（最大簇 {} 条）",
                len(usable), len(clusters), clusters[0].size if clusters else 0)
    return ClusteringResult(clusters=clusters, assignments=assignments)


async def label_clusters(result: ClusteringResult,
                         texts: dict[str, str],
                         *,
                         client=None) -> ClusteringResult:
    """用 LLM 为每个簇生成观点摘要，覆盖默认的截断文本标签。

    默认标签（取代表性文本前 16 字）可读性差——它复述了原文，
    而「这一簇在表达什么诉求」需要概括。一次调用处理所有簇，成本可控。
    """
    if not result.clusters:
        return result

    from engines.analytics.prompts import (
        CLUSTER_LABEL_SYSTEM,
        CLUSTER_LABEL_USER_TEMPLATE,
    )
    from engines.analytics.schemas import ClusterLabelling
    from engines.analytics.sentiment import default_client

    blocks = []
    for index, cluster in enumerate(result.clusters):
        lines = [f"簇 {index}（{cluster.size} 条，主导情感：{cluster.dominant_sentiment or '未知'}）"]
        for position, uid in enumerate(cluster.representative_ids[:3], start=1):
            text = (texts.get(uid) or "").replace("\n", " ").strip()
            lines.append(f"  {position}. {text[:110]}")
        blocks.append("\n".join(lines))

    prompt = CLUSTER_LABEL_USER_TEMPLATE.format(
        count=len(result.clusters), clusters="\n\n".join(blocks))

    llm = client or default_client()
    try:
        labelling = await llm.generate_object(prompt, ClusterLabelling,
                                              system=CLUSTER_LABEL_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        from engines.common.logging import mask_secret

        logger.warning("簇命名失败，保留默认标签：{} | {}",
                       type(exc).__name__, mask_secret(str(exc))[:220])
        return result

    if len(labelling.clusters) < len(result.clusters):
        logger.warning("簇命名返回 {} 条，期望 {} 条，未覆盖的簇保留默认标签",
                       len(labelling.clusters), len(result.clusters))

    # 模型可能不填 index（数组顺序已表达对应关系），按位置兜底
    by_index: dict[int, object] = {}
    for position, item in enumerate(labelling.clusters):
        by_index[item.index if item.index is not None else position] = item
    updated = []
    for index, cluster in enumerate(result.clusters):
        item = by_index.get(index)
        if item is None:
            updated.append(cluster)
            continue
        updated.append(ClusterInfo(
            cluster_id=cluster.cluster_id,
            label=(item.label or cluster.label)[:32],
            size=cluster.size,
            member_ids=cluster.member_ids,
            representative_ids=cluster.representative_ids,
            dominant_sentiment=cluster.dominant_sentiment,
            summary=item.summary,
        ))

    return ClusteringResult(clusters=updated, assignments=result.assignments,
                            method=result.method,
                            degraded_reason=result.degraded_reason)
