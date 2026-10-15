"""证据准备：多路召回 → 排序 → 聚类与维度路由。

三个节点合在一个模块，因为它们共享同一份中间产物（证据池），
拆成三个文件反而要在文件间来回跳。

**多路召回的通道设计**（每条通道解决一个盲区）：
- 关键词召回：命中主题词的内容，最直接但只认字面
- 评论召回：评论里藏着观点与情绪，是情感分析的主要材料
- 热度召回：高互动内容，代表事件的实际影响力
- 语义召回：命中「表达不同但意思相近」的内容，补关键词的漏

**情感标签复用而非重算**（设计规格 D-08）：标签在内容入库时已由 analytics
产出，这里只从 `content_analysis` 读回来。重算一遍不仅浪费，
更会造成「大屏显示负向 60%、报告里写负向 45%」的自相矛盾。
"""

from collections import defaultdict
from dataclasses import replace

import numpy as np

from engines.analytics.clustering import cluster_texts
from engines.analytics.embedding import Embedder
from engines.analytics.retrieval import vector_search
from engines.common.logging import logger
from engines.contracts.content import UnifiedContent
from engines.contracts.dimensions import DIMENSIONS, get_dimensions
from engines.contracts.evidence import (
    CHANNEL_COMMENT,
    CHANNEL_HOT,
    CHANNEL_KEYWORD,
    CHANNEL_QUOTAS,
    CHANNEL_SEMANTIC,
    MAX_EVIDENCE_RECORDS,
    EvidencePool,
    EvidenceRecord,
    RetrievalMeta,
    score_evidence,
)
from engines.ingestion.reader import FetchQuery, fetch_contents
from engines.insight_agent.state import InsightState

# 语义召回条数：比最终证据池上限大一些，留出融合与筛选的余地
VECTOR_TOP_K = 40
# 章节规划时预览的代表性材料条数
PLAN_PREVIEW_COUNT = 12


def _to_evidence(content: UnifiedContent, channel: str, rank: int,
                 matched_queries: tuple[str, ...],
                 labels: dict[str, dict]) -> EvidenceRecord:
    """把统一内容模型转成证据记录。"""
    label = labels.get(content.content_uid, {})
    return EvidenceRecord(
        record_id=content.content_uid,
        content_uid=content.content_uid,
        source_type=content.source_type,
        source_table=content.source_table,
        title=content.title,
        content=content.display_text,
        author_nickname=content.author.nickname,
        published_at=content.published_at,
        url=content.url,
        source_keyword=content.source_keyword,
        record_type=content.record_type,
        engagement=content.engagement,
        hotness_score=float(content.engagement.total),
        retrieval=RetrievalMeta(
            matched_queries=matched_queries,
            retrieval_channels=(channel,),
            # 排名越靠前分数越高（1.0 递减到 0.1）
            retrieval_scores={channel: max(0.1, 1.0 - (rank - 1) * 0.03)},
        ),
        sentiment=str(label.get("sentiment", "") or ""),
        risk_level=str(label.get("risk_level", "") or ""),
        intensity_index=float(label.get("intensity_index", 0) or 0),
    )


async def retrieval_node(state: InsightState) -> dict:
    """多路召回，产出证据池。"""
    from engines.analytics import repository
    from engines.common.db.session import session_scope

    query = state["query"]
    keyword = state.get("topic_keyword") or query

    # ---- 通道 1~3：私域库（关键词 / 评论 / 热度）----
    fetched = await fetch_contents(FetchQuery(keyword=keyword))
    contents = fetched.contents
    posts = [c for c in contents if c.record_type == "post"]
    comments = [c for c in contents if c.record_type == "comment"]
    hottest = sorted(posts, key=lambda c: -c.engagement.total)

    # ---- 读回已产出的情感标签（复用，不重算）----
    async with session_scope() as session:
        labels = await repository.fetch_labels(
            session, [c.content_uid for c in contents])

    # ---- 通道 4：语义召回 ----
    vector_hits = await vector_search(query, top_k=VECTOR_TOP_K)
    vector_uids = [hit.content_uid for hit in vector_hits]

    records: list[EvidenceRecord] = []
    seen: set[str] = set()

    def _add(content: UnifiedContent, channel: str, rank: int) -> None:
        if content.content_uid in seen:
            return
        seen.add(content.content_uid)
        records.append(_to_evidence(content, channel, rank, (query,), labels))

    for rank, content in enumerate(posts, start=1):
        _add(content, CHANNEL_KEYWORD, rank)
    for rank, content in enumerate(comments, start=1):
        _add(content, CHANNEL_COMMENT, rank)
    for rank, content in enumerate(hottest, start=1):
        _add(content, CHANNEL_HOT, rank)

    # 语义召回的内容可能在私域库里没有（比如来自其他主题），
    # 这类只保留标识与分数，正文以向量库里的副本为准
    by_uid = {c.content_uid: c for c in contents}
    for rank, hit in enumerate(vector_hits, start=1):
        if hit.content_uid in seen:
            continue
        seen.add(hit.content_uid)
        content = by_uid.get(hit.content_uid)
        if content is not None:
            records.append(_to_evidence(content, CHANNEL_SEMANTIC, rank, (query,), labels))
        else:
            records.append(EvidenceRecord(
                record_id=hit.content_uid, content_uid=hit.content_uid,
                source_type=_source_of(hit.source_type),
                content=hit.content, published_at=hit.published_at,
                hotness_score=hit.hotness_score,
                retrieval=RetrievalMeta(matched_queries=(query,),
                                        retrieval_channels=(CHANNEL_SEMANTIC,),
                                        retrieval_scores={CHANNEL_SEMANTIC: 0.8}),
            ))

    stats = {
        "keyword_recall": len(posts),
        "comment_recall": len(comments),
        "hot_recall": len(hottest),
        "semantic_recall": len(vector_uids),
        "total": len(records),
    }
    logger.info("私域召回完成：{} 条（{}）", len(records), stats)
    return {"pool": EvidencePool(query=query, records=records),
            "retrieval_stats": stats}


def _source_of(value: str):
    from engines.contracts.enums import SourceType

    try:
        return SourceType(value)
    except ValueError:
        return SourceType.SOCIAL


async def rank_node(state: InsightState) -> dict:
    """去重、打分、按通道配额筛选。

    配额的意义是**保证每个通道都有代表**：纯按分数截断时，
    高热度内容会把评论类证据全部挤掉，而评论恰恰是情绪与观点分析的主要材料。
    """
    pool: EvidencePool = state["pool"]
    if not pool.records:
        return {"pool": pool}

    # ---- 同一内容被多通道命中时合并（保留分数最高的通道信息）----
    merged: dict[str, EvidenceRecord] = {}
    for record in pool.records:
        existing = merged.get(record.content_uid)
        if existing is None:
            merged[record.content_uid] = record
            continue
        channels = tuple(dict.fromkeys(
            existing.retrieval.retrieval_channels + record.retrieval.retrieval_channels))
        scores = {**record.retrieval.retrieval_scores, **existing.retrieval.retrieval_scores}
        merged[record.content_uid] = replace(
            existing,
            retrieval=RetrievalMeta(
                matched_queries=tuple(dict.fromkeys(
                    existing.retrieval.matched_queries
                    + record.retrieval.matched_queries)),
                retrieval_channels=channels,
                retrieval_scores=scores),
            hotness_score=max(existing.hotness_score, record.hotness_score))

    records = list(merged.values())
    max_hotness = max((r.hotness_score for r in records), default=0.0)

    scored = [replace(r, final_score=score_evidence(r, max_hotness)) for r in records]
    scored.sort(key=lambda r: -r.final_score)

    # ---- 按通道配额挑选 ----
    quota: dict[str, int] = dict(CHANNEL_QUOTAS)
    picked: list[EvidenceRecord] = []
    bucket_count: dict[str, int] = defaultdict(int)

    for record in scored:
        channel = record.retrieval.primary_channel
        if bucket_count[channel] >= quota.get(channel, 10):
            continue
        bucket_count[channel] += 1
        picked.append(record)
        if len(picked) >= MAX_EVIDENCE_RECORDS:
            break

    # 配额没用满时用高分剩余补齐
    if len(picked) < MAX_EVIDENCE_RECORDS:
        picked_uid = {r.content_uid for r in picked}
        for record in scored:
            if len(picked) >= MAX_EVIDENCE_RECORDS:
                break
            if record.content_uid not in picked_uid:
                picked.append(record)

    pool.records = picked
    logger.info("证据排序完成：{} 条入选（上限 {}），配额分布 {}",
                len(picked), MAX_EVIDENCE_RECORDS, dict(bucket_count))
    return {"pool": pool}


async def cluster_node(state: InsightState) -> dict:
    """语义聚类并把簇路由到五个研究维度。

    路由用 embedding 相似度：维度锚点文本（标题 + 关键词）与簇的代表文本
    各编码一次，取相似度最高的维度。关键词匹配在这个场景下不够用——
    「客服打三次电话都在踢皮球」这类内容不含任何维度关键词，
    但语义上明确属于「公众情感与观点」。
    """
    pool: EvidencePool = state["pool"]
    if not pool.records:
        return {"pool": pool}

    items = [(r.content_uid, r.display_text) for r in pool.records]
    sentiments = {r.content_uid: r.sentiment for r in pool.records if r.sentiment}

    clustering = await cluster_texts(items, sentiments=sentiments)
    if not clustering.ok:
        # 聚类失败：全部归到「背景与概览」，至少让章节生成能继续
        fallback_key = get_dimensions()[0].key
        pool.records = [replace(r, cluster_id=f"cluster_{fallback_key}")
                        for r in pool.records]
        logger.warning("聚类不可用（{}），证据全部归入 {}",
                       clustering.degraded_reason, fallback_key)
        return {"pool": pool}

    texts = dict(items)
    assignment = await _route_clusters(clustering.clusters, texts)
    default_key = get_dimensions()[0].key

    # ⚠️ 这里是「内容 → 簇」与「簇 → 维度」两次映射的衔接点。
    # 曾经漏掉第一步，直接读 record.cluster_id（此时还是 None），
    # 于是所有证据都落到了默认维度——报告照常生成，只是四个维度空空如也。
    uid_to_cluster = clustering.assignments

    # 注意：本项目的运行环境是 Python 3.11，f-string 表达式**不能跨行**
    # （PEP 701 的跨行 f-string 要 3.12+），所以这里用显式循环而不是推导式
    pool.clusters = clustering.clusters
    routed_records: list[EvidenceRecord] = []
    for record in pool.records:
        dimension_key = assignment.get(
            uid_to_cluster.get(record.content_uid, ""), default_key)
        routed_records.append(replace(record, cluster_id=f"cluster_{dimension_key}"))
    pool.records = routed_records

    distribution: dict[str, int] = {}
    for record in pool.records:
        key = (record.cluster_id or "").removeprefix("cluster_")
        distribution[key] = distribution.get(key, 0) + 1
    logger.info("聚类完成：{} 簇 → 证据分布 {}", len(clustering.clusters), distribution)
    return {"pool": pool}


async def _route_clusters(clusters, texts: dict[str, str]) -> dict[str, str]:
    """簇 → 维度 key 的映射。用 embedding 相似度匹配维度锚点。"""
    dimensions = get_dimensions()
    embedder = Embedder.from_settings()

    anchors = [f"{d.title}：{d.insight_goal} {' '.join(d.cluster_keywords)}"
               for d in dimensions]
    cluster_samples = [
        " ".join(texts.get(uid, "") for uid in cluster.representative_ids[:3])[:400]
        for cluster in clusters
    ]

    try:
        anchor_vectors = np.asarray(await embedder.embed(anchors), dtype=np.float32)
        cluster_vectors = np.asarray(await embedder.embed(cluster_samples), dtype=np.float32)
    except Exception as exc:  # noqa: BLE001
        logger.warning("维度路由编码失败（{}），按顺序轮流分配", type(exc).__name__)
        return {c.cluster_id: dimensions[i % len(dimensions)].key
                for i, c in enumerate(clusters)}

    # L2 归一化后内积即余弦相似度
    def _norm(m):
        return m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-8)

    similarities = _norm(cluster_vectors) @ _norm(anchor_vectors).T
    best = similarities.argmax(axis=1)
    return {cluster.cluster_id: dimensions[int(idx)].key
            for cluster, idx in zip(clusters, best)}
