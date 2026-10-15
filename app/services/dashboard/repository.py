"""大盘指标的取数与物化读写。

| 职责 | 说明 |
|------|------|
| 取数 | 从 `content_analysis` / `entity` / `topic_keyword` / `alert_record` / `ticket` 聚合 |
| 物化 | `dashboard_metrics` 的 upsert 与读取（覆盖式，只保留最新一份） |

**取数一律是聚合查询**（COUNT / GROUP BY），不把明细拉进内存：
`content_analysis` 在演示规模是两千多行，但生产会到十万级，那时
「取回全部行再在 Python 里数」会直接把大盘拖垮。
唯一的例外是热词——它需要词与频次的对应关系，用 GROUP BY 一次取回聚合结果即可。
"""

import json
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import func, select

from app.services.dashboard.metrics import AlertRow, MetricRow
from engines.common.db.models import (
    AlertRecord,
    ContentAnalysis,
    DashboardMetric,
    Entity,
    Ticket,
    TopicKeyword,
)

__all__ = [
    "MetricSnapshot",
    "DashboardRepository",
    "SqlDashboardRepository",
    "SCOPE_ALL",
]

# 全局范围用 'all' 而不是 NULL（见 DDL 注释：NULL 会破坏唯一索引的 upsert 语义）
SCOPE_ALL = "all"


@dataclass(frozen=True)
class MetricSnapshot:
    metric_key: str
    scope: str
    bucket: str
    payload: dict[str, Any]
    computed_at: int
    expires_at: int

    @property
    def expired(self) -> bool:
        return self.expires_at <= int(time.time())


class DashboardRepository(Protocol):
    """大盘取数与物化接口。内存替身与 SQL 实现共用。"""

    async def analysis_rows(self, *, topic_id: str | None = None,
                            start: int | None = None) -> list[MetricRow]: ...
    async def alert_rows(self, *, since: int | None = None) -> list[AlertRow]: ...
    async def open_ticket_count(self) -> int: ...
    async def entity_mentions(self, *, topic_id: str | None = None) -> Counter[str]: ...
    async def keyword_mentions(self, *, topic_id: str | None = None) -> Counter[str]: ...
    async def read_metric(self, metric_key: str, *, scope: str,
                          bucket: str) -> MetricSnapshot | None: ...
    async def read_scope(self, scope: str) -> list[MetricSnapshot]: ...
    async def upsert_metric(self, metric_key: str, *, scope: str, bucket: str,
                            payload: dict, ttl_seconds: int) -> MetricSnapshot: ...


class SqlDashboardRepository:
    """基于 SQLAlchemy 异步会话的实现。"""

    def __init__(self, session) -> None:
        self._session = session

    # ---- 取数 ----

    async def analysis_rows(self, *, topic_id: str | None = None,
                            start: int | None = None) -> list[MetricRow]:
        stmt = select(ContentAnalysis.content_uid, ContentAnalysis.source_type,
                      ContentAnalysis.sentiment, ContentAnalysis.risk_level,
                      ContentAnalysis.topic_id, ContentAnalysis.analyzed_at)
        if topic_id:
            stmt = stmt.where(ContentAnalysis.topic_id == topic_id)
        if start is not None:
            stmt = stmt.where(ContentAnalysis.analyzed_at >= start)
        rows = (await self._session.execute(stmt)).all()
        return [MetricRow(content_uid=uid, source_type=source_type, sentiment=sentiment,
                          risk_level=risk_level, topic_id=row_topic, analyzed_at=analyzed)
                for uid, source_type, sentiment, risk_level, row_topic, analyzed in rows]

    async def alert_rows(self, *, since: int | None = None) -> list[AlertRow]:
        stmt = select(AlertRecord.level, AlertRecord.status, AlertRecord.event_id)
        if since is not None:
            stmt = stmt.where(AlertRecord.triggered_at >= since)
        rows = (await self._session.execute(stmt)).all()
        return [AlertRow(level=level, status=status, event_id=event_id)
                for level, status, event_id in rows]

    async def open_ticket_count(self) -> int:
        return int((await self._session.execute(
            select(func.count()).select_from(Ticket)
            .where(Ticket.status.notin_(("resolved", "ignored"))))).scalar() or 0)

    async def entity_mentions(self, *, topic_id: str | None = None) -> Counter[str]:
        """实体提及来源。

        实体表没有 topic_id（它是跨事件的），因此按主题过滤时用**事件关联**近似：
        这里给的是全局词频，主题维度由调用方决定是否使用（见 service 的说明）。
        """
        rows = (await self._session.execute(
            select(Entity.name, func.count()).group_by(Entity.name))).all()
        return Counter({name: int(count) for name, count in rows if name})

    async def keyword_mentions(self, *, topic_id: str | None = None) -> Counter[str]:
        """词包词频：某主题下每个词的权重（使用者关心的对象，权重略高）。"""
        stmt = select(TopicKeyword.word, func.max(TopicKeyword.weight))
        if topic_id:
            stmt = stmt.where(TopicKeyword.topic_id == topic_id)
        rows = (await self._session.execute(
            stmt.group_by(TopicKeyword.word))).all()
        return Counter({word: float(weight or 1) for word, weight in rows if word})

    # ---- 物化 ----

    async def read_metric(self, metric_key: str, *, scope: str,
                          bucket: str) -> MetricSnapshot | None:
        row = (await self._session.execute(
            select(DashboardMetric)
            .where(DashboardMetric.metric_key == metric_key,
                   DashboardMetric.scope == scope,
                   DashboardMetric.bucket == bucket))).scalar_one_or_none()
        return _to_snapshot(row) if row else None

    async def read_scope(self, scope: str) -> list[MetricSnapshot]:
        rows = (await self._session.execute(
            select(DashboardMetric).where(DashboardMetric.scope == scope))).scalars().all()
        return [_to_snapshot(row) for row in rows]

    async def upsert_metric(self, metric_key: str, *, scope: str, bucket: str,
                            payload: dict, ttl_seconds: int) -> MetricSnapshot:
        """写入或覆盖（同一 key+scope+bucket 只保留最新一份）。"""
        now = int(time.time())
        expires_at = now + max(1, ttl_seconds)
        text = json.dumps(payload, ensure_ascii=False)

        row = (await self._session.execute(
            select(DashboardMetric)
            .where(DashboardMetric.metric_key == metric_key,
                   DashboardMetric.scope == scope,
                   DashboardMetric.bucket == bucket))).scalar_one_or_none()
        if row is None:
            row = DashboardMetric(metric_key=metric_key, scope=scope, bucket=bucket,
                                  payload=text, computed_at=now, expires_at=expires_at)
            self._session.add(row)
        else:
            row.payload = text
            row.computed_at = now
            row.expires_at = expires_at
        await self._session.flush()
        return _to_snapshot(row)


def _to_snapshot(row: DashboardMetric) -> MetricSnapshot:
    try:
        payload = json.loads(row.payload or "{}")
    except (TypeError, ValueError):
        # 物化内容损坏：当作空对象（下次刷新会覆盖），不让大盘整体 500
        payload = {}
    return MetricSnapshot(metric_key=row.metric_key, scope=row.scope, bucket=row.bucket,
                          payload=payload if isinstance(payload, dict) else {},
                          computed_at=row.computed_at, expires_at=row.expires_at)
