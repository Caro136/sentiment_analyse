"""`content_analysis` 表的读写。

写入按 `content_uid` 幂等：同一内容重复打标（模型调整后重跑）应当更新结果，
而不是撞唯一索引报错。这也让"重跑分析"成为一个安全操作。
"""

import time
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from engines.common.db.models import (
    ClusterMember,
    ContentAnalysis,
    Entity,
    EntityRelation,
    Event,
    EventContent,
    OpinionCluster,
)

# 可直接写入的列（防止调用方传入不存在的键导致运行时错误）
_WRITABLE_COLUMNS = {
    "content_uid", "source_type", "source_table", "business_id", "topic_id",
    "sentiment", "emotion", "sentiment_confidence", "intensity_index",
    "risk_level", "risk_factors", "is_manual_override", "override_reason",
    "simhash", "is_suspected_bot", "model_version", "analyzed_at",
}

# 人工复核过的结果不该被自动打标覆盖（FR-24：人工结论优先）
_PRESERVE_WHEN_OVERRIDDEN = {"sentiment", "emotion", "risk_level", "intensity_index"}


async def save_labels(session: AsyncSession, payloads: list[dict]) -> int:
    """幂等写入打标结果。返回写入条数。"""
    if not payloads:
        return 0

    uids = [p["content_uid"] for p in payloads]
    existing_rows = await session.execute(
        select(ContentAnalysis).where(ContentAnalysis.content_uid.in_(uids)))
    existing = {row.content_uid: row for row in existing_rows.scalars()}

    now = int(time.time())
    written = 0
    for payload in payloads:
        uid = payload["content_uid"]
        row = existing.get(uid)
        if row is None:
            row = ContentAnalysis(content_uid=uid, analyzed_at=now)
            session.add(row)
        elif row.is_manual_override:
            # 已人工复核：保留结论，只刷新模型版本与时间，便于追踪
            kept = {k: v for k, v in payload.items()
                    if k not in _PRESERVE_WHEN_OVERRIDDEN}
            for key, value in kept.items():
                if key in _WRITABLE_COLUMNS:
                    setattr(row, key, value)
            continue

        for key, value in payload.items():
            if key in _WRITABLE_COLUMNS:
                setattr(row, key, value)
        written += 1

    await session.flush()
    return written


async def load_analyzed_uids(session: AsyncSession,
                             content_uids: list[str]) -> set[str]:
    """查询其中已完成打标的标识，用于跳过重复分析。"""
    if not content_uids:
        return set()
    result = await session.execute(
        select(ContentAnalysis.content_uid)
        .where(ContentAnalysis.content_uid.in_(content_uids)))
    return {row[0] for row in result}


async def fetch_labels(session: AsyncSession,
                       content_uids: list[str]) -> dict[str, dict]:
    """取回打标结果（供内容列表与大盘聚合使用）。"""
    if not content_uids:
        return {}
    result = await session.execute(
        select(ContentAnalysis).where(ContentAnalysis.content_uid.in_(content_uids)))
    return {row.content_uid: row.to_dict() for row in result.scalars()}


async def count_by_sentiment(session: AsyncSession) -> dict[str, int]:
    """按情感倾向统计条数（大盘指标用）。"""
    from sqlalchemy import func

    result = await session.execute(
        select(ContentAnalysis.sentiment, func.count())
        .group_by(ContentAnalysis.sentiment))
    return {row[0]: row[1] for row in result}


async def count_by_risk_level(session: AsyncSession) -> dict[str, int]:
    """按风险等级统计条数。"""
    result = await session.execute(
        select(ContentAnalysis.risk_level, func.count())
        .group_by(ContentAnalysis.risk_level))
    return {row[0]: row[1] for row in result}


# ---------------------------------------------------------------------------
# 事件（FR-48）
# ---------------------------------------------------------------------------

def make_event_id(scope: str) -> str:
    """事件 ID 由主题标识派生，保证「一个主题一个事件专题」且幂等。"""
    return f"evt_{scope}"


async def ensure_event(session: AsyncSession, scope: str, *, title: str = "",
                       topic_id: str | None = None,
                       sync_title: bool = False) -> str:
    """确保事件存在，返回 event_id。

    `scope` 通常是主题关键词——一个监测主题对应一个事件专题。
    更细的子事件拆分（同一主题下的多个子话题）留给事件研判阶段。

    **标题为什么要同步（`sync_title=True`）**：事件标题是人读的名字，而它同时是
    「从事件发起报告」时加载三方产物的键（`report_engine` 按 query 找
    `insight_*` / `media_*` / `host_*`）。事件由预警链路创建时只用得上主题关键词
    （`evt_<keyword>` 的 ID 稳定，标题当时随手写成关键词），而研判用的是**主题名称**——
    两者不一致时报告会以「主持人研判、私域分析、公域分析 的产物尚未生成」失败，
    且看不出是键不一致（T-34 端到端联调实测踩到）。因此由知道主题名称的调用方
    （监测链路）显式开启同步，预警链路不开启——免得多方来回改写标题。
    """
    event_id = make_event_id(scope)
    existing = (await session.execute(
        select(Event).where(Event.event_id == event_id))).scalar_one_or_none()
    if existing is None:
        session.add(Event(
            event_id=event_id,
            title=title or scope,
            topic_id=topic_id or scope,
            status="monitoring",
            created_at=int(time.time()),
        ))
        await session.flush()
    elif sync_title and title and existing.title != title:
        existing.title = title
        await session.flush()
    return event_id


async def attach_contents(session: AsyncSession, event_id: str,
                          content_uids: Sequence[str]) -> int:
    """把内容关联到事件。已关联的跳过（幂等）。"""
    if not content_uids:
        return 0
    existing = {row[0] for row in await session.execute(
        select(EventContent.content_uid).where(EventContent.event_id == event_id))}
    now = int(time.time())
    added = 0
    for uid in content_uids:
        if uid in existing:
            continue
        session.add(EventContent(event_id=event_id, content_uid=uid, added_at=now))
        added += 1
    await session.flush()
    return added


# ---------------------------------------------------------------------------
# 观点聚类（FR-28）
# ---------------------------------------------------------------------------

def scoped_cluster_id(scope: str, cluster_id: str) -> str:
    """给簇 ID 加作用域前缀。

    聚类算法产出的 ID 是 `cluster_0`/`cluster_1` 这种序号，
    在不同主题间必然重复——直接落库会让两个主题的簇互相覆盖。
    """
    return f"{scope}:{cluster_id}"


async def save_clusters(session: AsyncSession, clusters, *, scope: str,
                        event_id: str | None = None,
                        topic_id: str | None = None) -> int:
    """幂等写入观点簇及其成员。

    先按作用域清空旧成员再写入：簇成员表没有唯一约束，
    重复写入会不断累积，导致「一个簇有几百个成员」的假象。
    """
    if not clusters:
        return 0

    scoped_ids = [scoped_cluster_id(scope, c.cluster_id) for c in clusters]
    await session.execute(
        delete(ClusterMember).where(ClusterMember.cluster_id.in_(scoped_ids)))

    now = int(time.time())
    existing_clusters = {row.cluster_id: row for row in (await session.execute(
        select(OpinionCluster).where(OpinionCluster.cluster_id.in_(scoped_ids))
    )).scalars()}

    for cluster, scoped_id in zip(clusters, scoped_ids):
        row = existing_clusters.get(scoped_id)
        if row is None:
            row = OpinionCluster(cluster_id=scoped_id, created_at=now)
            session.add(row)
        row.event_id = event_id
        row.topic_id = topic_id
        row.label = cluster.label[:128]
        row.summary = cluster.summary or None
        row.size = cluster.size
        row.sentiment = cluster.dominant_sentiment or None

        representatives = set(cluster.representative_ids)
        for uid in cluster.member_ids:
            session.add(ClusterMember(cluster_id=scoped_id, content_uid=uid,
                                      is_representative=uid in representatives))

    await session.flush()
    return len(clusters)


# ---------------------------------------------------------------------------
# 实体与关系（FR-25 / FR-26）
# ---------------------------------------------------------------------------

async def save_entities(session: AsyncSession, extraction, *,
                        event_id: str | None = None) -> tuple[int, int]:
    """幂等写入实体与关系，返回 (实体数, 关系数)。"""
    if not extraction.entities and not extraction.relations:
        return 0, 0

    now = int(time.time())

    # ---- 实体：按 entity_id upsert ----
    entity_ids = [e.entity_id for e in extraction.entities]
    existing_entities = {row.entity_id: row for row in (await session.execute(
        select(Entity).where(Entity.entity_id.in_(entity_ids))
    )).scalars()} if entity_ids else {}

    for node in extraction.entities:
        row = existing_entities.get(node.entity_id)
        if row is None:
            session.add(Entity(entity_id=node.entity_id, name=node.name[:128],
                               entity_type=node.entity_type, created_at=now))

    # ---- 关系：按 relation_id upsert ----
    relation_ids = [r.relation_id for r in extraction.relations]
    existing_relations = {row.relation_id: row for row in (await session.execute(
        select(EntityRelation).where(EntityRelation.relation_id.in_(relation_ids))
    )).scalars()} if relation_ids else {}

    for edge in extraction.relations:
        row = existing_relations.get(edge.relation_id)
        if row is None:
            session.add(EntityRelation(
                relation_id=edge.relation_id, event_id=event_id,
                from_entity_id=edge.from_entity_id, to_entity_id=edge.to_entity_id,
                relation_type=edge.relation_type, weight=edge.weight,
                created_at=now))
        else:
            # 共现权重会随新内容累加，取较大值（重跑分析时不该被覆盖成小值）
            row.weight = max(float(row.weight or 0), edge.weight)

    await session.flush()
    return len(extraction.entities), len(extraction.relations)


async def fetch_event(entity_id: str, session: AsyncSession) -> dict | None:
    row = (await session.execute(
        select(Event).where(Event.event_id == event_id))).scalar_one_or_none()
    return row.to_dict() if row else None


async def fetch_entity_graph(session: AsyncSession, event_id: str,
                             limit: int = 60) -> dict:
    """取回实体图谱数据（供 FR-46 的图谱渲染）。

    权重降序取前 N 条关系，再取其两端实体——全量返回会让前端图变成一团毛线。
    """
    edges = (await session.execute(
        select(EntityRelation).where(EntityRelation.event_id == event_id)
        .order_by(EntityRelation.weight.desc()).limit(limit))).scalars().all()

    node_ids = {e.from_entity_id for e in edges} | {e.to_entity_id for e in edges}
    nodes = (await session.execute(
        select(Entity).where(Entity.entity_id.in_(node_ids)))).scalars().all() \
        if node_ids else []

    return {
        "nodes": [{"id": n.entity_id, "name": n.name, "type": n.entity_type}
                  for n in nodes],
        "edges": [{"source": e.from_entity_id, "target": e.to_entity_id,
                   "type": e.relation_type, "weight": float(e.weight or 0)}
                  for e in edges],
    }
