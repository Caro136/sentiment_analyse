"""主持人的 LangGraph 图。

    解析 → 累积 → 找就绪 →(有) 逐维研判 → 记录 →(五维完成) 最终裁判 → 落盘

两个条件出口体现了「增量研判」的核心：每次事件驱动一次图调用，
**没有就绪的维度就什么都不做**（继续等另一侧），五维未满也不生成最终裁判。
这样一个图定义同时覆盖了「单边到达」「配对就绪」「全部完成」三种执行路径。
"""

from langgraph.graph import END, START, StateGraph

from engines.host_agent.nodes import (
    accumulate_section,
    find_ready_pairs,
    generate_final_report,
    judge_section,
    parse_section,
    record_judgement,
    route_after_find,
    route_after_record,
    save_report,
)
from engines.host_agent.state import HostState

# 单次事件最多推进：解析+累积+查找+研判+记录+最终+落盘 ≈ 7 步，
# 20 足够且能挡住意外循环
DEFAULT_RECURSION_LIMIT = 20


def build_graph():
    builder = StateGraph(HostState)

    builder.add_node("parse", parse_section)
    builder.add_node("accumulate", accumulate_section)
    builder.add_node("find", find_ready_pairs)
    builder.add_node("judge", judge_section)
    builder.add_node("record", record_judgement)
    builder.add_node("final", generate_final_report)
    builder.add_node("save", save_report)

    builder.add_edge(START, "parse")
    builder.add_edge("parse", "accumulate")
    builder.add_edge("accumulate", "find")

    # 没有维度就绪：本次事件只是一侧到达，直接结束等下一次
    builder.add_conditional_edges("find", route_after_find,
                                  {"judge": "judge", "end": END})

    builder.add_edge("judge", "record")

    # 五维未完成就结束；完成了才走最终裁判与落盘
    builder.add_conditional_edges("record", route_after_record,
                                  {"final": "final", "end": END})

    builder.add_edge("final", "save")
    builder.add_edge("save", END)

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
