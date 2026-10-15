"""私域研究员的图状态。

LangGraph 的状态是**节点之间唯一的通信媒介**——节点不直接调彼此，
只读写状态。因此状态里放什么，决定了流程能表达多复杂的分支与循环。

两个关键字段：
  - `cursor`：章节生成的游标。五维章节是**循环逐章生成**的，
    而不是一次性生成整篇——这样每完成一章就能发事件、前端能滚动看到进度，
    主持人也能尽早开始配对研判（FR-68）。
  - `section_evidence`：维度 → 证据的映射。调拨的结果，
    章节生成时按维度直接取，不必每章重新筛选。
"""

from dataclasses import dataclass, field
from typing import TypedDict

from engines.contracts.evidence import EvidencePool
# SectionDraft 由两个分析角色共用，定义在 contracts；
# 这里重新导出，保持既有导入路径可用
from engines.contracts.section import SectionDraft

__all__ = ["SectionDraft", "ResearchPlan", "InsightState"]


@dataclass
class ResearchPlan:
    """章节规划结果。"""

    overview: str = ""
    # 有序的 (维度 key, 标题, 分析要点)
    sections: list[tuple[str, str, list[str]]] = field(default_factory=list)

    @property
    def keys(self) -> list[str]:
        return [key for key, _, _ in self.sections]

    def title_of(self, section_key: str) -> str:
        for key, title, _ in self.sections:
            if key == section_key:
                return title
        return section_key

    def focus_of(self, section_key: str) -> list[str]:
        for key, _, focus in self.sections:
            if key == section_key:
                return focus
        return []


class InsightState(TypedDict, total=False):
    """图状态。`total=False` 让节点可以只返回自己更新的字段。"""

    # ---- 输入 ----
    query: str                      # 研判主题词
    topic_keyword: str              # 数据侧的主题关键词（通常与 query 相同）

    # ---- 证据准备 ----
    pool: EvidencePool              # 召回 + 排序后的证据池
    retrieval_stats: dict           # 各通道召回数，用于诊断与展示

    # ---- 分析与规划 ----
    plan: ResearchPlan              # 章节规划
    section_evidence: dict          # 维度 key → 证据列表

    # ---- 章节产出 ----
    sections: list[SectionDraft]    # 已生成的章节
    cursor: int                     # 当前生成到第几章（游标循环）

    # ---- 产出 ----
    report_markdown: str
    report_path: str
    errors: list[str]
