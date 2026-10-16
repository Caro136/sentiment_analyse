"""研判任务的用例层（T-16 的接入部分，FR-64~66）。

**职责**：把「发起一次多智能体研判」这件事接到产品上——解析研判主题词、
调用编排层、把失败翻译成 HTTP 语义（已有任务在跑 → 409）、留审计。

**为什么需要它**：编排层（`engines/orchestration`）与两个研究员、主持人早就实现了，
但**没有任何接口能启动它**（设计规格 6.1 的 API-22/23 一直缺实现）。
结果是链路到「预警」就断了：预警出来了，却没人能发起研判，
报告也就永远拿不到三方产物、只会失败。T-34 端到端联调发现了这个断点。

**不做**：不实现角色内部流程（那是两个 Agent 的 Graph），不聚合报告
（`report_engine` 读三方产物落盘）。
"""

from .service import (
    ResearchRunning,
    get_task,
    resolve_query,
    start_research,
)

__all__ = [
    "start_research",
    "get_task",
    "resolve_query",
    "ResearchRunning",
]
