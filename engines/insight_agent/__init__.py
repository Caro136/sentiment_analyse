"""私域研究员（T-13，对应 PRD FR-64 / FR-67 / FR-68）。

职责：面向平台自有舆情库，执行
     多路召回（关键词 / 评论 / 热度 / 语义）→ 证据池排序 → 语义聚类与维度路由
     → 五维章节规划 → 证据调拨 → 游标循环逐章生成 → 排版 → 落盘。

不做：访问公网（那是 media_agent 的职责）。

⚠️ **不得重复实现情感分析**（设计规格 D-08）：本模块从 `content_analysis`
   读回 analytics 已产出的标签。重算会造成「大屏显示负向 60%、报告里写负向 45%」
   的自相矛盾，且浪费一次模型调用。
⚠️ 与 media_agent 互不依赖，两者只通过 contracts 共享契约。
"""

from engines.insight_agent.agent import InsightResult, run_insight_agent
from engines.insight_agent.graph import build_graph, get_graph, reset_graph
from engines.insight_agent.state import InsightState, ResearchPlan, SectionDraft

__all__ = [
    "run_insight_agent", "InsightResult",
    "build_graph", "get_graph", "reset_graph",
    "InsightState", "ResearchPlan", "SectionDraft",
]
