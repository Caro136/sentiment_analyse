"""监测配置仓储：`topic` / `topic_keyword` / `source_channel` / `topic_channel` 的读写。

与认证模块同样的分层理由（见 `app/services/auth/repository.py`）：SQL 收在一处，
服务层不出现 SQLAlchemy，测试可以换成内存替身实现，鉴权与用例逻辑才可能离线跑。

**批量取词包**（`list_keywords_for`）是刻意的：列表接口若逐主题查词包，
20 条主题就是 20 次往返；一次 `IN` 查询取回后在内存分组即可。
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import delete, func, select

from engines.common.db.models import SourceChannel, Topic, TopicChannel, TopicKeyword

__all__ = [
    "KeywordRecord",
    "TopicRecord",
    "ChannelRecord",
    "MonitoringRepository",
    "SqlMonitoringRepository",
    "new_topic_id",
    "new_channel_id",
]


@dataclass(frozen=True)
class KeywordRecord:
    word: str
    group_type: str
    weight: float


@dataclass(frozen=True)
class TopicRecord:
    topic_id: str
    name: str
    description: str
    source_keyword: str | None
    owner_id: str
    status: str
    start_at: int | None
    created_at: int


@dataclass(frozen=True)
class ChannelRecord:
    channel_id: str
    channel_type: str
    name: str
    enabled: bool
    poll_interval_min: int
    lookback_hours: int
    updated_at: int
    last_collected_at: int | None = None
    last_collected_count: int | None = None


@dataclass
class TopicBundle:
    """主题 + 词包 + 关联通道——服务层组装的完整视图。"""

    topic: TopicRecord
    keywords: list[KeywordRecord] = field(default_factory=list)
    channel_ids: list[str] = field(default_factory=list)


def new_topic_id() -> str:
    """主题业务ID。与其他业务标识同构（前缀 + 时间 + 随机），便于日志辨识。"""
    import secrets
    import time

    return f"tp{int(time.time())}{secrets.token_hex(3)}"


def new_channel_id(channel_type: str) -> str:
    """通道业务ID。按信源类型派生，稳定可读（`ch_social_media`）。"""
    return f"ch_{channel_type}"


class MonitoringRepository(Protocol):
    """监测配置读写接口。内存替身与 SQL 实现共用。"""

    async def list_topics(self, *, page: int, page_size: int, status: str | None = None,
                          owner_id: str | None = None, keyword: str | None = None,
                          ) -> tuple[int, list[TopicRecord]]: ...

    async def get_topic(self, topic_id: str) -> TopicRecord | None: ...

    async def create_topic(self, record: TopicRecord) -> TopicRecord: ...

    async def update_topic(self, topic_id: str, fields: dict) -> TopicRecord | None: ...

    async def list_keywords(self, topic_id: str) -> list[KeywordRecord]: ...

    async def list_keywords_for(self, topic_ids: list[str],
                                ) -> dict[str, list[KeywordRecord]]: ...

    async def replace_keywords(self, topic_id: str,
                               keywords: list[KeywordRecord]) -> None: ...

    async def list_channels(self) -> list[ChannelRecord]: ...

    async def get_channel(self, channel_id: str) -> ChannelRecord | None: ...

    async def create_channel(self, record: ChannelRecord) -> ChannelRecord: ...

    async def update_channel(self, channel_id: str, fields: dict) -> ChannelRecord | None: ...

    async def list_topic_channel_ids(self, topic_id: str) -> list[str]: ...

    async def replace_topic_channels(self, topic_id: str, channel_ids: list[str]) -> None: ...


def _to_topic(row: Topic) -> TopicRecord:
    return TopicRecord(
        topic_id=row.topic_id,
        name=row.name,
        description=row.description or "",
        source_keyword=row.source_keyword,
        owner_id=row.owner_id,
        status=row.status,
        start_at=row.start_at,
        created_at=row.created_at,
    )


def _to_channel(row: SourceChannel) -> ChannelRecord:
    return ChannelRecord(
        channel_id=row.channel_id,
        channel_type=row.channel_type,
        name=row.name,
        enabled=bool(row.enabled),
        poll_interval_min=row.poll_interval_min,
        lookback_hours=row.lookback_hours,
        updated_at=row.updated_at,
        last_collected_at=row.last_collected_at,
        last_collected_count=row.last_collected_count,
    )


class SqlMonitoringRepository:
    """基于 SQLAlchemy 异步会话的实现。事务边界由调用方的 `session_scope()` 决定。"""

    def __init__(self, session) -> None:
        self._session = session

    # ---- 主题 ----
    async def list_topics(self, *, page: int, page_size: int, status: str | None = None,
                          owner_id: str | None = None, keyword: str | None = None,
                          ) -> tuple[int, list[TopicRecord]]:
        conditions = []
        if status:
            conditions.append(Topic.status == status)
        if owner_id:
            conditions.append(Topic.owner_id == owner_id)
        if keyword:
            conditions.append(Topic.name.like(f"%{keyword}%"))

        total_stmt = select(func.count()).select_from(Topic)
        rows_stmt = select(Topic).order_by(Topic.created_at.desc(), Topic.id.desc())
        for condition in conditions:
            total_stmt = total_stmt.where(condition)
            rows_stmt = rows_stmt.where(condition)

        total = int((await self._session.execute(total_stmt)).scalar() or 0)
        rows = (await self._session.execute(
            rows_stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
        return total, [_to_topic(r) for r in rows]

    async def get_topic(self, topic_id: str) -> TopicRecord | None:
        row = (await self._session.execute(
            select(Topic).where(Topic.topic_id == topic_id))).scalar_one_or_none()
        return _to_topic(row) if row else None

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        row = Topic(
            topic_id=record.topic_id,
            name=record.name,
            description=record.description,
            source_keyword=record.source_keyword,
            owner_id=record.owner_id,
            status=record.status,
            start_at=record.start_at,
            created_at=record.created_at,
        )
        self._session.add(row)
        await self._session.flush()      # 唯一约束冲突在此暴露，而不是等提交
        return _to_topic(row)

    async def update_topic(self, topic_id: str, fields: dict) -> TopicRecord | None:
        row = (await self._session.execute(
            select(Topic).where(Topic.topic_id == topic_id))).scalar_one_or_none()
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        return _to_topic(row)

    # ---- 词包 ----
    async def list_keywords(self, topic_id: str) -> list[KeywordRecord]:
        rows = (await self._session.execute(
            select(TopicKeyword).where(TopicKeyword.topic_id == topic_id)
            .order_by(TopicKeyword.group_type, TopicKeyword.id))).scalars().all()
        return [KeywordRecord(word=r.word, group_type=r.group_type,
                              weight=float(r.weight)) for r in rows]

    async def list_keywords_for(self, topic_ids: list[str],
                                ) -> dict[str, list[KeywordRecord]]:
        if not topic_ids:
            return {}
        rows = (await self._session.execute(
            select(TopicKeyword).where(TopicKeyword.topic_id.in_(topic_ids))
            .order_by(TopicKeyword.group_type, TopicKeyword.id))).scalars().all()

        grouped: dict[str, list[KeywordRecord]] = {tid: [] for tid in topic_ids}
        for row in rows:
            grouped.setdefault(row.topic_id, []).append(
                KeywordRecord(word=row.word, group_type=row.group_type,
                              weight=float(row.weight)))
        return grouped

    async def replace_keywords(self, topic_id: str,
                               keywords: list[KeywordRecord]) -> None:
        await self._session.execute(
            delete(TopicKeyword).where(TopicKeyword.topic_id == topic_id))
        for keyword in keywords:
            self._session.add(TopicKeyword(
                topic_id=topic_id, word=keyword.word,
                group_type=keyword.group_type, weight=keyword.weight))
        await self._session.flush()

    # ---- 通道 ----
    async def list_channels(self) -> list[ChannelRecord]:
        rows = (await self._session.execute(
            select(SourceChannel).order_by(SourceChannel.id))).scalars().all()
        return [_to_channel(r) for r in rows]

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        row = (await self._session.execute(
            select(SourceChannel).where(SourceChannel.channel_id == channel_id))
        ).scalar_one_or_none()
        return _to_channel(row) if row else None

    async def create_channel(self, record: ChannelRecord) -> ChannelRecord:
        row = SourceChannel(
            channel_id=record.channel_id,
            channel_type=record.channel_type,
            name=record.name,
            enabled=record.enabled,
            poll_interval_min=record.poll_interval_min,
            lookback_hours=record.lookback_hours,
            last_collected_at=record.last_collected_at,
            last_collected_count=record.last_collected_count,
            updated_at=record.updated_at,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_channel(row)

    async def update_channel(self, channel_id: str, fields: dict) -> ChannelRecord | None:
        row = (await self._session.execute(
            select(SourceChannel).where(SourceChannel.channel_id == channel_id))
        ).scalar_one_or_none()
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        return _to_channel(row)

    # ---- 主题 × 通道 ----
    async def list_topic_channel_ids(self, topic_id: str) -> list[str]:
        rows = (await self._session.execute(
            select(TopicChannel.channel_id)
            .where(TopicChannel.topic_id == topic_id)
            .order_by(TopicChannel.id))).scalars().all()
        return list(rows)

    async def replace_topic_channels(self, topic_id: str,
                                     channel_ids: list[str]) -> None:
        await self._session.execute(
            delete(TopicChannel).where(TopicChannel.topic_id == topic_id))
        for channel_id in dict.fromkeys(channel_ids):      # 去重且保持顺序
            self._session.add(TopicChannel(topic_id=topic_id, channel_id=channel_id))
        await self._session.flush()
