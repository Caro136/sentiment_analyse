"""私域研究员的 LangGraph 图。

    检索 → 排序 → 聚类 → 规划 → 调拨 →(游标循环) 逐章生成 → 排版 → 落盘

用图而不是一条长函数表达这条链路，好处是**循环与条件分支显式可见**：
「逐章生成」是一个带条件出口的循环节点，看图的形状就知道它会转五圈；
写成 `for` 循环则要读到函数体里才发现。
"""

from langgraph.graph import END, START, StateGraph

from engines.insight_agent.reporting import (
    allocation_node,
    format_node,
    persist_node,
    plan_node,
    route_after_summarize,
    summarize_node,
)
from engines.insight_agent.retrieval import (
    cluster_node,
    rank_node,
    retrieval_node,
)
from engines.insight_agent.state import InsightState

# 节点名（供日志、事件与调试引用）
NODE_RETRIEVAL = "retrieval"
NODE_RANK = "rank"
NODE_CLUSTER = "cluster"
NODE_PLAN = "plan"
NODE_ALLOCATION = "allocation"
NODE_SUMMARIZE = "summarize"
NODE_FORMAT = "format"
NODE_PERSIST = "persist"

# 递归上限：五维章节循环各占一步，加上前后处理，30 足够且能防住意外的死循环
DEFAULT_RECURSION_LIMIT = 30


def build_graph():
    """构建并编译私域研究员的状态图。"""
    builder = StateGraph(InsightState)

    builder.add_node(NODE_RETRIEVAL, retrieval_node)
    builder.add_node(NODE_RANK, rank_node)
    builder.add_node(NODE_CLUSTER, cluster_node)
    builder.add_node(NODE_PLAN, plan_node)
    builder.add_node(NODE_ALLOCATION, allocation_node)
    builder.add_node(NODE_SUMMARIZE, summarize_node)
    builder.add_node(NODE_FORMAT, format_node)
    builder.add_node(NODE_PERSIST, persist_node)

    builder.add_edge(START, NODE_RETRIEVAL)
    builder.add_edge(NODE_RETRIEVAL, NODE_RANK)
    builder.add_edge(NODE_RANK, NODE_CLUSTER)
    builder.add_edge(NODE_CLUSTER, NODE_PLAN)
    builder.add_edge(NODE_PLAN, NODE_ALLOCATION)
    builder.add_edge(NODE_ALLOCATION, NODE_SUMMARIZE)

    # 游标循环：还有章节就回到自己，否则去排版
    builder.add_conditional_edges(
        NODE_SUMMARIZE,
        route_after_summarize,
        {NODE_SUMMARIZE: NODE_SUMMARIZE, NODE_FORMAT: NODE_FORMAT},
    )

    builder.add_edge(NODE_FORMAT, NODE_PERSIST)
    builder.add_edge(NODE_PERSIST, END)

    return builder.compile()


_graph = None


def get_graph():
    """编译结果的单例（编译有开销，每次调用重建是浪费）。"""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_graph() -> None:
    """丢弃已编译的图（测试或依赖变更后使用）。"""
    global _graph
    _graph = None
