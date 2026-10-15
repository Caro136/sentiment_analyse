"""系统运维服务（T-28，对应 PRD FR-72~FR-75）。

| 模块 | 职责 |
|------|------|
| `tasks`      | `task_run` 读写 + 统一埋点（`track_task` 上下文管理器） |
| `logs`       | 日志尾部检索（按模块/级别/关键字/trace_id/时间范围，读出口脱敏） |
| `reset`      | 演示数据重置（白名单表、确认口令、保护账号与审计） |
| `retention`  | 按留存周期清理**分析结果**（CLI；原始区永不清理由 ADR-03 决定） |

两类边界：

1. **`system:read` 只给管理员**（PRD 第 2 章：查看系统日志与任务状态是管理员职责），
   重置也走同一权限 + 确认口令，不新增权限点；
2. **运维入口不做「顺手的数据修复」**：能删的只有本模块白名单里的表，
   原始区与账号审计永不触碰。
"""

from app.services.system.reset import RESET_SCOPES, preview_reset, reset_demo_data
from app.services.system.tasks import TASK_TYPES, SqlTaskRepository, track_task

__all__ = [
    "TASK_TYPES", "SqlTaskRepository", "track_task",
    "RESET_SCOPES", "preview_reset", "reset_demo_data",
]
