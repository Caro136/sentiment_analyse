"""ORM 模型集合。

导入本模块会触发所有模型向 `Base.metadata` 注册——建表、一致性校验、
后续的元数据操作都依赖这一点。新增模型后必须在此导出。
"""

from engines.common.db.models.analysis import (
    ClusterMember,
    ContentAnalysis,
    Entity,
    EntityRelation,
    Event,
    EventContent,
    ModalityAsset,
    OpinionCluster,
    SpreadNode,
)
from engines.common.db.models.business import (
    AlertRecord,
    AlertRule,
    AuditLog,
    Report,
    ReportFile,
    SourceChannel,
    SysUser,
    TaskRun,
    Ticket,
    TicketLog,
    Topic,
    TopicChannel,
    TopicKeyword,
)

__all__ = [
    # 分析区
    "ContentAnalysis", "ModalityAsset", "Event", "EventContent",
    "OpinionCluster", "ClusterMember", "SpreadNode", "Entity", "EntityRelation",
    # 业务区
    "SysUser", "AuditLog", "Topic", "TopicKeyword", "SourceChannel", "TopicChannel",
    "AlertRule", "AlertRecord", "Ticket", "TicketLog", "Report", "ReportFile", "TaskRun",
]
