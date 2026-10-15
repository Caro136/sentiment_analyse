"""公域研究员的 LangGraph 图。

    规划 → 搜索 →(游标循环) 逐章生成 → 排版 → 落盘

比私域少三个节点（那边要做召回、排序、聚类）：
公域的材料来自搜索 API，检索本身就是「召回 + 排序」，
不需要再做一轮证据池处理。这个差异是数据特性决定的，不是设计取舍。
"""

from langgraph.graph import END, START, StateGraph

from engines.media_agent.nodes import (
    format_node,
    persist_node,
    plan_node,
    route_after_summarize,
    search_node,
    summarize_node,
)
from engines.media_agent.state import MediaState

NODE_PLAN = "plan"
NODE_SEARCH = "search"
NODE_SUMMARIZE = "summarize"
NODE_FORMAT = "format"
NODE_PERSIST = "persist"

DEFAULT_RECURSION_LIMIT = 30


def build_graph():
    """构建并编译公域研究员的状态图。"""
    builder = StateGraph(MediaState)

    builder.add_node(NODE_PLAN, plan_node)
    builder.add_node(NODE_SEARCH, search_node)
    builder.add_node(NODE_SUMMARIZE, summarize_node)
    builder.add_node(NODE_FORMAT, format_node)
    builder.add_node(NODE_PERSIST, persist_node)

    builder.add_edge(START, NODE_PLAN)
    builder.add_edge(NODE_PLAN, NODE_SEARCH)
    builder.add_edge(NODE_SEARCH, NODE_SUMMARIZE)

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
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_graph() -> None:
    global _graph
    _graph = None
