"""分析区 ORM 模型。

权威定义是 `sql/analysis/01_analysis_schema.sql`，本文件是它的代码映射。
改表结构时先改 DDL，再改这里，最后跑一致性校验。

约定：所有分析表通过 `content_uid` 关联原始内容，格式为
`{source_type}:{source_table}:{business_id}`。
"""

from sqlalchemy import BigInteger, Boolean, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from engines.common.db.base import Base


class ContentAnalysis(Base):
    """内容分析标签：认知分析的落库结果，大盘 / 筛选 / 预警的公共数据源。"""

    __tablename__ = "content_analysis"
    __table_args__ = (
        Index("uk_content_analysis_uid", "content_uid", unique=True),
        Index("ix_content_analysis_filter", "source_type", "sentiment", "risk_level"),
        Index("ix_content_analysis_time", "analyzed_at"),
        Index("ix_content_analysis_topic", "topic_id"),
        {"comment": "内容分析标签"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    business_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(64))
    sentiment: Mapped[str] = mapped_column(String(16), nullable=False)
    emotion: Mapped[str | None] = mapped_column(String(16))
    sentiment_confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    intensity_index: Mapped[float | None] = mapped_column(Numeric(5, 4))
    risk_level: Mapped[str | None] = mapped_column(String(2))
    risk_factors: Mapped[str | None] = mapped_column(Text)
    is_manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override_reason: Mapped[str | None] = mapped_column(Text)
    simhash: Mapped[str | None] = mapped_column(String(64))
    is_suspected_bot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_version: Mapped[str | None] = mapped_column(String(64))
    analyzed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ModalityAsset(Base):
    """多模态解析产物：OCR / ASR 结果是独立证据，与原文并列。"""

    __tablename__ = "modality_asset"
    __table_args__ = (
        Index("uk_modality_asset_asset_id", "asset_id", unique=True),
        Index("ix_modality_asset_content", "content_uid"),
        Index("ix_modality_asset_status", "parse_status"),
        {"comment": "多模态解析产物"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    origin_url: Mapped[str | None] = mapped_column(Text)
    parsed_text: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    parser: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parsed_at: Mapped[int | None] = mapped_column(BigInteger)


class Event(Base):
    """事件专题：同一事件的讨论聚合。"""

    __tablename__ = "event"
    __table_args__ = (
        Index("uk_event_event_id", "event_id", unique=True),
        Index("ix_event_topic_status", "topic_id", "status"),
        Index("ix_event_risk", "risk_level"),
        {"comment": "事件专题"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    topic_id: Mapped[str | None] = mapped_column(String(64))
    risk_level: Mapped[str | None] = mapped_column(String(2))
    intensity_index: Mapped[float | None] = mapped_column(Numeric(5, 4))
    first_seen_at: Mapped[int | None] = mapped_column(BigInteger)
    last_seen_at: Mapped[int | None] = mapped_column(BigInteger)
    total_volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_ratio: Mapped[float | None] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="monitoring")
    # FR-24 人工复核：`risk_level` 被人工改写时置 1，理由必填。
    # 算法原始结论不另存一列——它每次都由关联内容现算（见 app/services/event），
    # 因此天然「保留双份记录」：表里是人工值，算出的是算法值。
    manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    override_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EventContent(Base):
    """事件与内容的多对多关联。"""

    __tablename__ = "event_content"
    __table_args__ = (
        Index("uk_event_content", "event_id", "content_uid", unique=True),
        Index("ix_event_content_uid", "content_uid"),
        {"comment": "事件内容关联"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    relevance: Mapped[float | None] = mapped_column(Numeric(5, 4))
    added_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class OpinionCluster(Base):
    """观点聚类：核心争议焦点与主流诉求。"""

    __tablename__ = "opinion_cluster"
    __table_args__ = (
        Index("uk_opinion_cluster_cluster_id", "cluster_id", unique=True),
        Index("ix_opinion_cluster_event", "event_id"),
        {"comment": "观点聚类"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(64))
    topic_id: Mapped[str | None] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sentiment: Mapped[str | None] = mapped_column(String(16))
    intent: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ClusterMember(Base):
    """观点簇成员。"""

    __tablename__ = "cluster_member"
    __table_args__ = (
        Index("ix_cluster_member_cluster", "cluster_id"),
        Index("ix_cluster_member_uid", "content_uid"),
        {"comment": "观点簇成员"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cluster_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    is_representative: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class SpreadNode(Base):
    """传播溯源节点：首发、引爆、扩散等关键节点。"""

    __tablename__ = "spread_node"
    __table_args__ = (
        Index("uk_spread_node_node_id", "node_id", unique=True),
        Index("ix_spread_node_event_time", "event_id", "occurred_at"),
        {"comment": "传播溯源节点"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    content_uid: Mapped[str] = mapped_column(String(255), nullable=False)
    node_role: Mapped[str] = mapped_column(String(16), nullable=False)
    account_weight: Mapped[float | None] = mapped_column(Numeric(6, 2))
    occurred_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Entity(Base):
    """舆情实体：机构、人物、产品、事件、地点。"""

    __tablename__ = "entity"
    __table_args__ = (
        Index("uk_entity_name_type", "name", "entity_type", unique=True),
        Index("ix_entity_type", "entity_type"),
        {"comment": "舆情实体"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class EntityRelation(Base):
    """实体关系：传播关联链路图谱的边。"""

    __tablename__ = "entity_relation"
    __table_args__ = (
        Index("uk_entity_relation_id", "relation_id", unique=True),
        Index("ix_entity_relation_event", "event_id"),
        Index("ix_entity_relation_from", "from_entity_id"),
        Index("ix_entity_relation_to", "to_entity_id"),
        {"comment": "实体关系"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    relation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(64))
    from_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    to_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    weight: Mapped[float | None] = mapped_column(Numeric(6, 4))
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
