"""研判编排（T-16，对应 PRD FR-68 / FR-69 / FR-71）。

职责：
  - 按角色注册表并发启动分析角色（私域研究员 / 公域研究员）
  - 发布角色进度、结果与错误事件（驱动 SSE 与前端进度条）
  - 管理主持人运行时（先启动监听，再启动角色）
  - 维护研判任务状态

不做：实现角色内部流程（各 Agent 自己的 Graph 负责）。

两个硬性约束：
  1. **主持人必须先于分析角色启动**——反了会丢掉最早的章节事件，
     表现为「主持人永远差一维、最终裁判出不来」，且不报任何错；
  2. **同时只允许一个研判任务在跑**——主持人会话共享，
     两个主题并发会让同名维度被错误配对。
"""

from engines.orchestration.registry import (
    ResearchRole,
    get_registry,
    resolve_roles,
    role_names,
)
from engines.orchestration.research import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    ResearchOrchestrator,
    ResearchTask,
    RoleOutcome,
    get_orchestrator,
    reset_orchestrator,
)

__all__ = [
    "ResearchOrchestrator", "ResearchTask", "RoleOutcome",
    "get_orchestrator", "reset_orchestrator",
    "STATUS_RUNNING", "STATUS_COMPLETED", "STATUS_FAILED",
    "ResearchRole", "get_registry", "resolve_roles", "role_names",
]
