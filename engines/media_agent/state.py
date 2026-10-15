"""公域研究员的图状态。

与私域研究员的结构刻意保持对称（同样的 cursor 游标、同样的五维 section 列表），
因为两者的产出要交给同一个主持人按维度配对——
结构不一致的话，主持人得写两套解析逻辑。
"""

from dataclasses import dataclass, field
from typing import TypedDict

from engines.contracts.section import SectionDraft
from engines.media_agent.search import SearchResult


@dataclass
class MediaPlan:
    """检索计划：每个研究维度配一组检索词。"""

    overview: str = ""
    keywords_by_dimension: dict[str, list[str]] = field(default_factory=dict)

    def keywords_for(self, section_key: str) -> list[str]:
        return self.keywords_by_dimension.get(section_key, [])

    @property
    def total_keywords(self) -> int:
        return sum(len(v) for v in self.keywords_by_dimension.values())


class MediaState(TypedDict, total=False):
    """图状态。"""

    # ---- 输入 ----
    query: str

    # ---- 检索 ----
    plan: MediaPlan
    results: list[SearchResult]           # 全部去重后的搜索结果
    section_evidence: dict               # 维度 key → 搜索结果列表
    search_stats: dict

    # ---- 章节产出 ----
    sections: list[SectionDraft]
    cursor: int

    # ---- 产出 ----
    report_markdown: str
    report_path: str
    errors: list[str]
