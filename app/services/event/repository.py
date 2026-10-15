"""事件与溯源的仓储。

**几乎全是只读**：事件的建立归 analytics（`ensure_event`），标签归 analytics，
内容归接入层。查事件却顺手改了 `event` 表的统计列，是这类接口最容易被塞进来的私货，
因此统计与风险等级一律现算、不回写。

**唯一的写入是风险等级人工复核**（FR-24，`apply_risk_override`）：这是业务上
确实存在的动作——算法结论需要被人工纠正并留理由。

图谱数据直接复用 `engines.analytics.repository.fetch_entity_graph`
（T-11 已实现权重降序取前 N 条的逻辑），不在这里重写一遍。
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select

from engines.analytics.repository import fetch_entity_graph
from engines.common.db.models import (
    ClusterMember,
    ContentAnalysis,
    EntityRelation,
    Event,
    EventContent,
    OpinionCluster,
)

__all__ = [
    "EventRecord",
    "ClusterRecord",
    "EventRepository",
    "SqlEventRepository",
]


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    title: str
    topic_id: str | None
    status: str
    created_at: int
    risk_level: str | None = None
    manual_override: bool = False
    override_reason: str | None = None


@dataclass(frozen=True)
class ClusterRecord:
    cluster_id: str
    label: str
    summary: str
    size: int
    sentiment: str | None
    intent: str | None
    event_id: str | None


def _to_event(row: Event) -> EventRecord:
    return EventRecord(event_id=row.event_id, title=row.title,
                       topic_id=row.topic_id, status=row.status,
                       created_at=row.created_at,
                       risk_level=row.risk_level,
                       manual_override=bool(row.manual_override),
                       override_reason=row.override_reason)


class EventRepository(Protocol):
    """事件与聚类读接口。内存替身与 SQL 实现共用。"""

    async def list_events(self, *, topic_id: str | None = None,
                          status: str | None = None,
                          keyword: str | None = None) -> list[EventRecord]: ...

    async def get_event(self, event_id: str) -> EventRecord | None: ...

    async def content_uids(self, event_ids: list[str]) -> dict[str, list[str]]: ...

    async def cluster_counts(self, event_ids: list[str]) -> dict[str, int]: ...

    async def list_clusters(self, event_id: str) -> list[ClusterRecord]: ...

    async def get_cluster(self, cluster_id: str) -> ClusterRecord | None: ...

    async def cluster_members(self, cluster_id: str, *, page: int, page_size: int,
                              representatives_only: bool = False,
                              ) -> tuple[int, list[str]]: ...

    async def entity_graph(self, event_id: str, *, limit: int) -> dict: ...

    async def entity_counts(self, event_id: str) -> tuple[int, int]: ...

    async def apply_risk_override(self, event_id: str, *, risk_level: str,
                                  reason: str) -> EventRecord | None: ...


class SqlEventRepository:
    """基于 SQLAlchemy 异步会话的实现（全部 SELECT）。"""

    def __init__(self, session) -> None:
        self._session = session

    async def list_events(self, *, topic_id: str | None = None,
                          status: str | None = None,
                          keyword: str | None = None) -> list[EventRecord]:
        stmt = select(Event)
        if status:
            stmt = stmt.where(Event.status == status)
        if keyword:
            stmt = stmt.where(Event.title.like(f"%{keyword}%"))
        if topic_id:
            # 事件的归属主题由**其内容的归属**决定：`event.topic_id` 是建事件时用的关键词
            # （`evt_星耀XPhone15` 那一套），不是主题业务ID。按它筛会一条都查不到，
            # 因此这里先按内容归属反查事件，再取这些事件。
            event_ids = await self._events_of_topic(topic_id)
            if not event_ids:
                return []
            stmt = stmt.where(Event.event_id.in_(event_ids))

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_event(row) for row in rows]

    async def _events_of_topic(self, topic_id: str) -> list[str]:
        """按内容归属反查事件：`event_content` → `content_analysis.topic_id`。"""
        rows = (await self._session.execute(
            select(EventContent.event_id)
            .join(ContentAnalysis, ContentAnalysis.content_uid == EventContent.content_uid)
            .where(ContentAnalysis.topic_id == topic_id)
            .distinct())).scalars().all()
        return list(rows)

    async def get_event(self, event_id: str) -> EventRecord | None:
        row = (await self._session.execute(
            select(Event).where(Event.event_id == event_id))).scalar_one_or_none()
        return _to_event(row) if row else None

    async def content_uids(self, event_ids: list[str]) -> dict[str, list[str]]:
        if not event_ids:
            return {}
        rows = (await self._session.execute(
            select(EventContent.event_id, EventContent.content_uid)
            .where(EventContent.event_id.in_(event_ids))
            .order_by(EventContent.event_id, EventContent.added_at))).all()

        grouped: dict[str, list[str]] = {event_id: [] for event_id in event_ids}
        for event_id, content_uid in rows:
            grouped.setdefault(event_id, []).append(content_uid)
        return grouped

    async def cluster_counts(self, event_ids: list[str]) -> dict[str, int]:
        if not event_ids:
            return {}
        rows = (await self._session.execute(
            select(OpinionCluster.event_id, func.count())
            .where(OpinionCluster.event_id.in_(event_ids))
            .group_by(OpinionCluster.event_id))).all()
        return {event_id: int(count) for event_id, count in rows if event_id}

    async def list_clusters(self, event_id: str) -> list[ClusterRecord]:
        rows = (await self._session.execute(
            select(OpinionCluster).where(OpinionCluster.event_id == event_id)
            .order_by(OpinionCluster.size.desc(), OpinionCluster.id))).scalars().all()
        return [ClusterRecord(cluster_id=r.cluster_id, label=r.label,
                              summary=r.summary or "", size=int(r.size or 0),
                              sentiment=r.sentiment, intent=r.intent,
                              event_id=r.event_id) for r in rows]

    async def get_cluster(self, cluster_id: str) -> ClusterRecord | None:
        row = (await self._session.execute(
            select(OpinionCluster).where(OpinionCluster.cluster_id == cluster_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        return ClusterRecord(cluster_id=row.cluster_id, label=row.label,
                             summary=row.summary or "", size=int(row.size or 0),
                             sentiment=row.sentiment, intent=row.intent,
                             event_id=row.event_id)

    async def cluster_members(self, cluster_id: str, *, page: int, page_size: int,
                              representatives_only: bool = False,
                              ) -> tuple[int, list[str]]:
        conditions = [ClusterMember.cluster_id == cluster_id]
        if representatives_only:
            conditions.append(ClusterMember.is_representative.is_(True))

        total = int((await self._session.execute(
            select(func.count()).select_from(ClusterMember).where(*conditions))
        ).scalar() or 0)

        rows = (await self._session.execute(
            select(ClusterMember.content_uid).where(*conditions)
            .order_by(ClusterMember.is_representative.desc(), ClusterMember.id)
            .offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return total, list(rows)

    async def entity_graph(self, event_id: str, *, limit: int) -> dict:
        """复用 analytics 的图谱读取（权重降序取前 N 条关系 + 其两端实体）。"""
        return await fetch_entity_graph(self._session, event_id, limit=limit)

    async def entity_counts(self, event_id: str) -> tuple[int, int]:
        relations = int((await self._session.execute(
            select(func.count()).select_from(EntityRelation)
            .where(EntityRelation.event_id == event_id))).scalar() or 0)
        graph = await self.entity_graph(event_id, limit=1000)
        return len(graph.get("nodes", [])), relations

    async def apply_risk_override(self, event_id: str, *, risk_level: str,
                                  reason: str) -> EventRecord | None:
        """写入人工复核结果（FR-24）。

        只改 `risk_level` 与两列复核标记；**算法的原始结论不写回表**——
        它每读一次都由关联内容现算（`service.collect_stats`），
        因此「保留双份记录」是天然的：表里是人工值，算出的是算法值。
        """
        row = (await self._session.execute(
            select(Event).where(Event.event_id == event_id))).scalar_one_or_none()
        if row is None:
            return None
        row.risk_level = risk_level
        row.manual_override = True
        row.override_reason = reason
        await self._session.flush()
        return _to_event(row)
