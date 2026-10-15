"""主持人研判（T-15，对应 PRD FR-66 / FR-68）。

职责：监听章节就绪事件 → 按 section_key 把私域与公域的同维章节配对
     → 两侧到齐即逐维研判（共识 / 分歧 / 风险）→ 五维完成后生成最终裁判。

设计要点：
  1. **事件驱动、增量研判**：不等两侧报告全部完成，某一维度到齐就研判该维度；
  2. **回调 O(1)**：事件回调只入队，图调用交给 worker 协程串行处理；
  3. **双层状态**：单次图调用的 State 与跨事件累积的 Session，
     唯一接缝是 Session.to_state / apply_state；
  4. **跨主题隔离**：配对按 section_key 做，不同主题的章节必须隔离，否则会错配；
  5. 最终报告的源材料是**主持人自己的逐维研判结论**，而非原始章节堆叠。

不做：生成最终报告成品（交 report_engine）。
"""

from engines.host_agent.agent import HostAgent, HostStatus, reset_runtime
from engines.host_agent.graph import build_graph, get_graph, reset_graph
from engines.host_agent.listener import SectionReadyListener
from engines.host_agent.pair_store import REQUIRED_SOURCES, PairStore
from engines.host_agent.state import HostSession, HostState, SectionJudgement

__all__ = [
    "HostAgent", "HostStatus", "reset_runtime",
    "build_graph", "get_graph", "reset_graph",
    "SectionReadyListener",
    "PairStore", "REQUIRED_SOURCES",
    "HostSession", "HostState", "SectionJudgement",
]
