"""业务区 ORM 模型。

权威定义是 `sql/business/01_business_schema.sql`。
涵盖账号权限、监测配置、预警、处置工单、研判报告与任务状态。
"""

from sqlalchemy import BigInteger, Boolean, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from engines.common.db.base import Base


# ---------------------------------------------------------------------------
# 账号与审计
# ---------------------------------------------------------------------------

class SysUser(Base):
    """系统用户。口令只存哈希，任何情况下不得落明文。"""

    __tablename__ = "sys_user"
    __table_args__ = (
        Index("uk_sys_user_username", "username", unique=True),
        Index("uk_sys_user_user_id", "user_id", unique=True),
        {"comment": "系统用户"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_login_at: Mapped[int | None] = mapped_column(BigInteger)


class AuditLog(Base):
    """操作审计日志：配置变更、工单流转、报告导出、风险复核等留痕。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_user_time", "user_id", "operated_at"),
        Index("ix_audit_log_action", "action"),
        {"comment": "操作审计日志"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(255))
    before_value: Mapped[str | None] = mapped_column(Text)
    after_value: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    operated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# 监测配置
# ---------------------------------------------------------------------------

class Topic(Base):
    """监测主题。`source_keyword` 对应原始数据中的来源关键词，是主题归属的判定依据。"""

    __tablename__ = "topic"
    __table_args__ = (
        Index("uk_topic_topic_id", "topic_id", unique=True),
        Index("ix_topic_owner", "owner_id"),
        {"comment": "监测主题"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_keyword: Mapped[str | None] = mapped_column(String(128))
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="enabled")
    start_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TopicKeyword(Base):
    """主题词包。"""

    __tablename__ = "topic_keyword"
    __table_args__ = (
        Index("ix_topic_keyword_topic", "topic_id", "group_type"),
        {"comment": "主题词包"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    topic_id: Mapped[str] = mapped_column(String(64), nullable=False)
    word: Mapped[str] = mapped_column(String(128), nullable=False)
    group_type: Mapped[str] = mapped_column(String(16), nullable=False)
    weight: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, default=1.0)


class SourceChannel(Base):
    """信源采集通道。"""

    __tablename__ = "source_channel"
    __table_args__ = (
        Index("uk_source_channel_channel_id", "channel_id", unique=True),
        {"comment": "信源通道"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    poll_interval_min: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    lookback_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    last_collected_at: Mapped[int | None] = mapped_column(BigInteger)
    last_collected_count: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class TopicChannel(Base):
    """主题与信源通道的多对多关联。"""

    __tablename__ = "topic_channel"
    __table_args__ = (
        Index("uk_topic_channel", "topic_id", "channel_id", unique=True),
        {"comment": "主题与信源通道关联"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)


# ---------------------------------------------------------------------------
# 预警
# ---------------------------------------------------------------------------

class AlertRule(Base):
    """预警规则。threshold 为相对值（0.5 表示 50%）。"""

    __tablename__ = "alert_rule"
    __table_args__ = (
        Index("uk_alert_rule_rule_id", "rule_id", unique=True),
        {"comment": "预警规则"},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Numeric(10, 4))
    sensitive_words: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str] = mapped_column(String(2), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class AlertRecord(Base):
    """告警记录。

    `notify_status` / `notify_error` 记录外发通知的投递结果——
    告警本身先落库、再通知，邮件失败只降级不丢告警（设计规格 8.3）。
    """

    __tablename__ = "alert_record"
    __table_args__ = (
        Index("uk_alert_record_alert_id", "alert_id", unique=True),
        Index("ix_alert_record_status_time", "status", "triggered_at"),
        Index("ix_alert_record_level", "level"),
        Index("ix_alert_record_event", "event_id"),
        {"comment": "告警记录"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(64))
    event_id: Mapped[str | None] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unread")
    triggered_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    responded_at: Mapped[int | None] = mapped_column(BigInteger)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail: Mapped[str | None] = mapped_column(Text)
    notify_status: Mapped[str | None] = mapped_column(String(16))
    notify_error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# 处置工单
# ---------------------------------------------------------------------------

class Ticket(Base):
    """处置工单。状态机：pending → tracking → verified → debunked → resolved / ignored。"""

    __tablename__ = "ticket"
    __table_args__ = (
        Index("uk_ticket_ticket_id", "ticket_id", unique=True),
        Index("ix_ticket_status", "status"),
        Index("ix_ticket_event", "event_id"),
        {"comment": "处置工单"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    alert_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    assignee_id: Mapped[str | None] = mapped_column(String(64))
    risk_level: Mapped[str | None] = mapped_column(String(2))
    response_draft: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closed_at: Mapped[int | None] = mapped_column(BigInteger)


class TicketLog(Base):
    """工单流转记录：形成可追溯的处置轨迹。"""

    __tablename__ = "ticket_log"
    __table_args__ = (
        Index("ix_ticket_log_ticket", "ticket_id", "operated_at"),
        {"comment": "工单流转记录"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str | None] = mapped_column(String(16))
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    operated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# 研判报告
# ---------------------------------------------------------------------------

class Report(Base):
    """研判报告。error_message 供前端展示失败原因与重试入口（FR-62）。"""

    __tablename__ = "report"
    __table_args__ = (
        Index("uk_report_report_id", "report_id", unique=True),
        Index("ix_report_topic_time", "topic_id", "generated_at"),
        Index("ix_report_event", "event_id"),
        {"comment": "研判报告"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(64))
    topic_id: Mapped[str | None] = mapped_column(String(64))
    report_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="generating")
    error_message: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReportFile(Base):
    """报告产物文件（Markdown 原文同时入库，供在线预览而不依赖文件系统）。"""

    __tablename__ = "report_file"
    __table_args__ = (
        Index("uk_report_file_file_id", "file_id", unique=True),
        Index("ix_report_file_report", "report_id"),
        {"comment": "报告产物文件"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(String(64), nullable=False)
    report_id: Mapped[str] = mapped_column(String(64), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    content_md: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)


# ---------------------------------------------------------------------------
# 任务运行状态
# ---------------------------------------------------------------------------

class TaskRun(Base):
    """任务运行状态：采集、分析、研判、报告等长任务的执行记录（FR-72）。"""

    __tablename__ = "task_run"
    __table_args__ = (
        Index("uk_task_run_task_id", "task_id", unique=True),
        Index("ix_task_run_type_time", "task_type", "started_at"),
        Index("ix_task_run_status", "status"),
        {"comment": "任务运行状态"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    input_summary: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)


class DashboardMetric(Base):
    """大盘指标物化：同一 (metric_key, scope, bucket) 只保留最新一份。

    `scope` 用 `all` 表示全局而非 NULL——MySQL 唯一索引里 NULL 互不相等，
    用 NULL 会让 upsert 每次都插新行（见 DDL 注释）。
    """

    __tablename__ = "dashboard_metrics"
    __table_args__ = (
        Index("uk_dashboard_metric", "metric_key", "scope", "bucket", unique=True),
        Index("ix_dashboard_metrics_expires", "expires_at"),
        {"comment": "大盘指标物化"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(80), nullable=False, default="all")
    bucket: Mapped[str] = mapped_column(String(16), nullable=False, default="static")
    payload: Mapped[str] = mapped_column(MEDIUMTEXT, nullable=False)
    computed_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
