"""章节配对存储。

主持人的工作方式是**事件驱动、按维度增量研判**：不等到两侧报告全部完成，
而是某一维度的两个来源都到齐就立即研判该维度。

这样做的两个理由：
  1. 前端能滚动看到研判过程，而不是等几分钟后一次性出现全部结论；
  2. 五维研判可以并行推进，整体延迟由最慢的一维决定，而不是五维之和。

PairStore 承担三件事：配对、防重复、低质量去重。
"""

from dataclasses import dataclass, field

from engines.common.logging import logger
from engines.contracts.dimensions import DIMENSIONS, get_dimensions
from engines.contracts.section import SectionDraft

# 参与配对的来源。主持人只在这两个来源的同维章节之间做研判。
REQUIRED_SOURCES = ("insight", "media")

# 证据强度的排序权重，用于「同一维度收到多份时保留哪一份」
_STRENGTH_RANK = {"strong": 3, "medium": 2, "weak": 1, "none": 0, "": 0}


def _quality_score(draft: SectionDraft) -> tuple[int, int, int]:
    """章节质量三元组，越大越好。

    先比证据强度，再比命中条数，最后比正文长度——
    这个顺序不能反：一篇冗长但只基于 2 条材料的章节，
    不如下一篇简短但基于 30 条材料的章节可靠。
    """
    return (
        _STRENGTH_RANK.get(draft.strength, 0),
        int(draft.evidence_count or 0),
        len(draft.body or ""),
    )


@dataclass
class PairStore:
    """按 section_key 存放两侧章节。"""

    # {section_key: {source: SectionDraft}}
    results: dict[str, dict[str, SectionDraft]] = field(default_factory=dict)
    # 已研判完成的维度
    judged: set[str] = field(default_factory=set)
    query: str = ""

    def add(self, source: str, draft: SectionDraft) -> bool:
        """登记一个章节。返回是否是新登记（重复推送返回 False）。

        同一来源同一维度重复到达是正常的——重跑研究会重新推送。
        这里只保留质量更高的那一份，避免用后来的一份低质量结果覆盖已有的好结果。
        """
        bucket = self.results.setdefault(draft.section_key, {})
        existing = bucket.get(source)
        if existing is not None:
            if _quality_score(draft) <= _quality_score(existing):
                return False
            logger.info("维度 {} 的 {} 章节被更高质量的一份替换", draft.section_key, source)
        bucket[source] = draft
        return True

    def is_ready(self, section_key: str) -> bool:
        """两侧到齐且尚未研判过。"""
        if section_key in self.judged:
            return False
        bucket = self.results.get(section_key, {})
        return all(source in bucket for source in REQUIRED_SOURCES)

    def ready_keys(self) -> list[str]:
        """当前可以研判的维度，按五维固定顺序返回。"""
        return [d.key for d in get_dimensions() if self.is_ready(d.key)]

    def mark_judged(self, section_key: str) -> None:
        self.judged.add(section_key)

    def all_done(self) -> bool:
        """五维是否全部研判完成。"""
        return self.judged >= set(DIMENSIONS)

    def missing_sources(self, section_key: str) -> list[str]:
        """该维度还缺哪些来源的章节。"""
        bucket = self.results.get(section_key, {})
        return [s for s in REQUIRED_SOURCES if s not in bucket]

    def pair_of(self, section_key: str) -> dict[str, SectionDraft]:
        return self.results.get(section_key, {})

    def stats(self) -> dict:
        """配对状态概览，用于诊断与前端展示。"""
        return {
            "received": {k: sorted(v) for k, v in self.results.items()},
            "judged": sorted(self.judged),
            "ready": self.ready_keys(),
            "all_done": self.all_done(),
        }

    def clear(self) -> None:
        """清空会话。跨主题研究时必须调用——
        配对是按 section_key 做的，不清空会让上一个主题的章节与本主题错误配对。
        """
        self.results.clear()
        self.judged.clear()
        self.query = ""
