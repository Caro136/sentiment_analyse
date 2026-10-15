"""内容查询仓储：`content_analysis` 与 `topic` 的只读查询。

**只读**——本模块不写任何表（写标签发生在 analytics，主题配置在 monitoring）。
内容查询天然是读多写无，把它做成只读能排除一大类「查询接口顺手改了点东西」的问题。

原始表（正文、发布时间、账号）不在这里：那属于接入层的职责，
由 `engines/ingestion/reader.py` 提供（ADR-03 的两段查询）。
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select

from engines.common.db.models import ContentAnalysis, Topic

__all__ = ["AnalysisRecord", "ContentRepository", "SqlContentRepository", "in_chunks"]

# 单条 SQL 里 IN 子句的参数上限保护（与接入层的批量查询同一思路）
CHUNK_SIZE = 500


def in_chunks(items: list, size: int = CHUNK_SIZE) -> list[list]:
    """把长列表切成若干批，避免 IN 子句参数过多撑爆语句。"""
    return [items[i:i + size] for i in range(0, len(items), size)]


@dataclass(frozen=True)
class AnalysisRecord:
    """一条内容分析标签的快照。"""

    content_uid: str
    source_type: str
    source_table: str
    business_id: int
    topic_id: str | None
    sentiment: str
    emotion: str | None
    sentiment_confidence: float | None
    intensity_index: float | None
    risk_level: str | None
    risk_factors: str | None
    is_manual_override: bool
    override_reason: str | None
    simhash: str | None
    is_suspected_bot: bool
    model_version: str | None
    analyzed_at: int


def _to_record(row: ContentAnalysis) -> AnalysisRecord:
    return AnalysisRecord(
        content_uid=row.content_uid,
        source_type=row.source_type,
        source_table=row.source_table,
        business_id=row.business_id,
        topic_id=row.topic_id,
        sentiment=row.sentiment,
        emotion=row.emotion,
        sentiment_confidence=(float(row.sentiment_confidence)
                              if row.sentiment_confidence is not None else None),
        intensity_index=(float(row.intensity_index)
                         if row.intensity_index is not None else None),
        risk_level=row.risk_level,
        risk_factors=row.risk_factors,
        is_manual_override=bool(row.is_manual_override),
        override_reason=row.override_reason,
        simhash=row.simhash,
        is_suspected_bot=bool(row.is_suspected_bot),
        model_version=row.model_version,
        analyzed_at=row.analyzed_at,
    )


class ContentRepository(Protocol):
    """内容查询接口。内存替身与 SQL 实现共用。"""

    async def query_analysis(self, content_uids: list[str], *, topic_id: str | None = None,
                             sentiment: str | None = None, risk_level: str | None = None,
                             is_suspected_bot: bool | None = None,
                             is_manual_override: bool | None = None,
                             ) -> list[AnalysisRecord]: ...

    async def get_analysis(self, content_uid: str) -> AnalysisRecord | None: ...

    async def topic_names(self, topic_ids: list[str]) -> dict[str, str]: ...


class SqlContentRepository:
    """基于 SQLAlchemy 异步会话的实现。"""

    def __init__(self, session) -> None:
        self._session = session

    async def query_analysis(self, content_uids: list[str], *, topic_id: str | None = None,
                             sentiment: str | None = None, risk_level: str | None = None,
                             is_suspected_bot: bool | None = None,
                             is_manual_override: bool | None = None,
                             ) -> list[AnalysisRecord]:
        if not content_uids:
            return []

        conditions = []
        if topic_id:
            conditions.append(ContentAnalysis.topic_id == topic_id)
        if sentiment:
            conditions.append(ContentAnalysis.sentiment == sentiment)
        if risk_level:
            conditions.append(ContentAnalysis.risk_level == risk_level)
        if is_suspected_bot is not None:
            conditions.append(ContentAnalysis.is_suspected_bot == is_suspected_bot)
        if is_manual_override is not None:
            conditions.append(ContentAnalysis.is_manual_override == is_manual_override)

        records: list[AnalysisRecord] = []
        for batch in in_chunks(content_uids):
            stmt = select(ContentAnalysis).where(ContentAnalysis.content_uid.in_(batch))
            for condition in conditions:
                stmt = stmt.where(condition)
            rows = (await self._session.execute(stmt)).scalars().all()
            records.extend(_to_record(row) for row in rows)
        return records

    async def get_analysis(self, content_uid: str) -> AnalysisRecord | None:
        row = (await self._session.execute(
            select(ContentAnalysis).where(ContentAnalysis.content_uid == content_uid)
        )).scalar_one_or_none()
        return _to_record(row) if row else None

    async def topic_names(self, topic_ids: list[str]) -> dict[str, str]:
        ids = [t for t in dict.fromkeys(topic_ids) if t]
        if not ids:
            return {}
        rows = (await self._session.execute(
            select(Topic.topic_id, Topic.name).where(Topic.topic_id.in_(ids)))).all()
        return {topic_id: name for topic_id, name in rows}


async def count_analysis(session) -> int:
    """已打标内容总数（供运维页与自检展示）。"""
    return int((await session.execute(
        select(func.count()).select_from(ContentAnalysis))).scalar() or 0)
