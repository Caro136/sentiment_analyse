"""私域研究员入口。

调用方式与「跑一个函数」没区别，图的存在对调用方透明——
编排层（orchestration）只需要 await 这个函数，并订阅它发出的事件。
"""

import time
from dataclasses import dataclass, field

from engines.common.logging import logger
from engines.insight_agent.graph import DEFAULT_RECURSION_LIMIT, get_graph
from engines.insight_agent.state import SectionDraft


@dataclass
class InsightResult:
    """一次私域研究的产出。"""

    query: str = ""
    report_markdown: str = ""
    report_path: str = ""
    sections: list[SectionDraft] = field(default_factory=list)
    evidence_count: int = 0
    retrieval_stats: dict = field(default_factory=dict)
    duration: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.report_markdown)

    @property
    def section_count(self) -> int:
        return len(self.sections)

    @property
    def fallback_sections(self) -> int:
        """证据不足、只输出了占位正文的章节数。

        这个数字要暴露出来——它直接说明「这份报告有几个维度是没料的」。
        """
        return sum(1 for s in self.sections if s.is_fallback)

    def render(self) -> str:
        if self.error:
            return f"私域研究失败：{self.error}"
        return (f"私域研究完成：{self.section_count} 章"
                f"（其中 {self.fallback_sections} 章证据不足），"
                f"证据 {self.evidence_count} 条，耗时 {self.duration:.1f}s")


async def run_insight_agent(
    query: str,
    *,
    topic_keyword: str | None = None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> InsightResult:
    """执行一次私域舆情研究。

    `query` 是研判主题词，同时也是私域库的检索关键词；
    两者不同时用 `topic_keyword` 单独指定。
    """
    started = time.monotonic()
    initial = {
        "query": query,
        "topic_keyword": topic_keyword or query,
        "sections": [],
        "cursor": 0,
        "errors": [],
    }

    try:
        final_state = await get_graph().ainvoke(
            initial, {"recursion_limit": recursion_limit})
    except Exception as exc:  # noqa: BLE001
        logger.exception("私域研究执行失败：{}", type(exc).__name__)
        return InsightResult(query=query,
                             error=f"{type(exc).__name__}: {str(exc)[:200]}",
                             duration=time.monotonic() - started)

    pool = final_state.get("pool")
    result = InsightResult(
        query=query,
        report_markdown=final_state.get("report_markdown", ""),
        report_path=final_state.get("report_path", ""),
        sections=final_state.get("sections", []),
        evidence_count=len(pool.records) if pool else 0,
        retrieval_stats=final_state.get("retrieval_stats", {}),
        duration=time.monotonic() - started,
    )
    logger.info(result.render())
    return result
