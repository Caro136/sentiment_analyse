"""主持人的状态模型。

**双层状态**是这个模块最容易写错的地方：

- `HostState`（TypedDict）：单次图调用的状态。每个事件触发一次 ainvoke，
  状态在调用内流转，调用结束即丢弃。
- `HostSession`（dataclass）：**跨事件累积**的会话。经过 10 次事件
  （两次来源 × 五个维度）才收敛出最终报告，状态必须活在事件之间。

两者的唯一接缝是 `to_state` / `apply_state`：
调用前把会话投影成图状态，调用后把图状态写回会话。
不设这层接缝的话，要么把 PairStore 塞进 TypedDict（类型不匹配），
要么每次调用都重建会话（累积的状态全丢）。
"""

from dataclasses import dataclass, field
from typing import TypedDict

from engines.contracts.dimensions import get_dimensions
from engines.contracts.section import SectionDraft
from engines.host_agent.pair_store import PairStore


@dataclass
class SectionJudgement:
    """一个维度的研判结果。"""

    section_key: str
    title: str = ""
    consensus: list[str] = field(default_factory=list)
    divergence: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    summary: str = ""
    insight_strength: str = ""
    media_strength: str = ""

    def to_markdown(self) -> str:
        lines = [f"### {self.title or self.section_key}", "", self.summary or "（无结论）", ""]
        if self.consensus:
            lines.append("**共识**")
            lines.extend(f"- {item}" for item in self.consensus)
            lines.append("")
        if self.divergence:
            lines.append("**分歧**")
            lines.extend(f"- {item}" for item in self.divergence)
            lines.append("")
        if self.risks:
            lines.append("**风险与缺口**")
            lines.extend(f"- {item}" for item in self.risks)
            lines.append("")
        return "\n".join(lines)


@dataclass
class HostSession:
    """跨事件累积的主持人会话。"""

    query: str = ""
    pairs: PairStore = field(default_factory=PairStore)
    judgements: list[SectionJudgement] = field(default_factory=list)
    final_report: str = ""
    final_confidence: float = 0.0
    report_path: str = ""

    # ------------------------------------------------------------------
    # 与图状态的双向投影
    # ------------------------------------------------------------------

    def to_state(self, incoming: SectionDraft | None = None,
                 source: str = "") -> "HostState":
        """把会话投影成图状态，并带上本次事件携带的章节。"""
        return HostState(
            query=self.query,
            source=source,
            incoming_section_key=incoming.section_key if incoming else "",
            incoming_title=incoming.title if incoming else "",
            incoming_body=incoming.body if incoming else "",
            incoming_strength=incoming.strength if incoming else "",
            incoming_evidence_count=incoming.evidence_count if incoming else 0,
            judgements=list(self.judgements),
            final_report=self.final_report,
            pairs=self.pairs,
        )

    def apply_state(self, state: "HostState") -> None:
        """把图状态写回会话。"""
        if state.get("query"):
            self.query = state["query"]
            self.pairs.query = self.query
        self.judgements = list(state.get("judgements", self.judgements))
        if state.get("final_report"):
            self.final_report = state["final_report"]
        if state.get("final_confidence"):
            self.final_confidence = state["final_confidence"]
        if state.get("report_path"):
            self.report_path = state["report_path"]

    # ------------------------------------------------------------------

    @property
    def judgement_of(self, section_key: str) -> SectionJudgement | None:
        return next((j for j in self.judgements if j.section_key == section_key), None)

    @property
    def is_complete(self) -> bool:
        """五维研判完毕且已产出最终裁判。"""
        return self.pairs.all_done() and bool(self.final_report)

    def ordered_judgements(self) -> list[SectionJudgement]:
        """按五维固定顺序返回研判结论。"""
        order = {d.key: i for i, d in enumerate(get_dimensions())}
        return sorted(self.judgements, key=lambda j: order.get(j.section_key, 99))

    def clear(self) -> None:
        self.pairs.clear()
        self.judgements.clear()
        self.final_report = ""
        self.final_confidence = 0.0
        self.report_path = ""
        self.query = ""


class HostState(TypedDict, total=False):
    """单次图调用的状态。"""

    # 本次事件携带的章节（parse_section 节点的输入）
    query: str
    source: str
    incoming_section_key: str
    incoming_title: str
    incoming_body: str
    incoming_strength: str
    incoming_evidence_count: int

    # 配对与研判（跨节点共享）
    pairs: PairStore
    ready_keys: list[str]
    judgements: list[SectionJudgement]

    # 产出
    final_report: str
    final_confidence: float
    report_path: str
