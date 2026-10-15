"""公域研究员（T-14，对应 PRD FR-65）。

职责：面向公开网络搜索，执行
     五维检索词规划 → 调用搜索服务 → 结果归集去重
     → 游标循环逐章摘要 → 排版 → 落盘。

不做：访问私域舆情库（那是 insight_agent 的职责）。

搜索服务通过适配器封装（Tavily / Bocha / Anspire），由配置切换，
**失败时自动降级到其他已配置的供应商**——实测遇到过配置的供应商返回 403，
不降级的话整条公域链路会空转出一份没有材料的报告。
⚠️ 与 insight_agent 互不依赖，两者只通过 contracts 共享契约。
"""

from engines.media_agent.agent import MediaResult, run_media_agent
from engines.media_agent.graph import build_graph, get_graph, reset_graph
from engines.media_agent.search import (
    SearchProvider,
    SearchResult,
    SearchService,
    build_providers,
)
from engines.media_agent.state import MediaPlan, MediaState

__all__ = [
    "run_media_agent", "MediaResult",
    "build_graph", "get_graph", "reset_graph",
    "MediaState", "MediaPlan",
    "SearchService", "SearchResult", "SearchProvider", "build_providers",
]
