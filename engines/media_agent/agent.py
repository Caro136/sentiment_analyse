"""公域研究员入口。"""

import time
from dataclasses import dataclass, field

from engines.common.logging import logger
from engines.contracts.section import SectionDraft
from engines.media_agent.graph import DEFAULT_RECURSION_LIMIT, get_graph


@dataclass
class MediaResult:
    """一次公域研究的产出。"""

    query: str = ""
    report_markdown: str = ""
    report_path: str = ""
    sections: list[SectionDraft] = field(default_factory=list)
    result_count: int = 0
    search_stats: dict = field(default_factory=dict)
    duration: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.report_markdown)

    @property
    def empty_sections(self) -> int:
        """完全没检索到材料的章节数。

        公域链路最常见的失败是「搜索供应商不可用」，
        这个数字能让调用方一眼看出报告是不是空转出来的。
        """
        return sum(1 for s in self.sections if s.is_fallback)

    def render(self) -> str:
        if self.error:
            return f"公域研究失败：{self.error}"
        provider = self.search_stats.get("provider", "未知")
        return (f"公域研究完成：{len(self.sections)} 章"
                f"（其中 {self.empty_sections} 章无材料），"
                f"材料 {self.result_count} 条，供应商 {provider}，"
                f"耗时 {self.duration:.1f}s")


async def run_media_agent(
    query: str,
    *,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
) -> MediaResult:
    """执行一次公域舆情研究。"""
    started = time.monotonic()
    initial = {"query": query, "sections": [], "cursor": 0, "errors": []}

    try:
        final_state = await get_graph().ainvoke(
            initial, {"recursion_limit": recursion_limit})
    except Exception as exc:  # noqa: BLE001
        logger.exception("公域研究执行失败：{}", type(exc).__name__)
        return MediaResult(query=query,
                           error=f"{type(exc).__name__}: {str(exc)[:200]}",
                           duration=time.monotonic() - started)

    results = final_state.get("results") or []
    result = MediaResult(
        query=query,
        report_markdown=final_state.get("report_markdown", ""),
        report_path=final_state.get("report_path", ""),
        sections=final_state.get("sections", []),
        result_count=len(results),
        search_stats=final_state.get("search_stats", {}),
        duration=time.monotonic() - started,
    )
    logger.info(result.render())
    return result
