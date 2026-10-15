"""报告输入加载：读三方角色产物 + 汇总统计数据。

**报告引擎不调用任何 Agent**——它只读磁盘上已有的文件。这是刻意的解耦：
研究阶段产出材料，报告阶段聚合材料，两者互不干扰。

好处是报告可以反复重生成（调整模板、换模型）而不必重跑整个研判链；
代价是要处理「材料不全」的情况——某个角色跑了但没产出、或者产物是上一轮的。
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from engines.common.logging import logger
from engines.contracts.roles import ROLE_HOST, ROLE_INSIGHT, ROLE_MEDIA

# 各角色的产物文件前缀
_ROLE_PREFIX = {
    ROLE_HOST: "host_",
    ROLE_INSIGHT: "insight_",
    ROLE_MEDIA: "media_",
}


@dataclass
class ReportInputs:
    """报告的全部输入材料。"""

    query: str
    host_report: str = ""
    insight_report: str = ""
    media_report: str = ""
    stats: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    loaded_at: float = field(default_factory=time.time)

    @property
    def is_ready(self) -> bool:
        """三方产物齐备才认为可生成。

        PRD FR-62 要求「上游材料未就绪时给出明确提示」——
        与其生成一份只有一半材料的报告，不如明确告诉调用方缺什么。
        """
        return not self.missing

    def describe_missing(self) -> str:
        if not self.missing:
            return ""
        names = {"host": "主持人研判", "insight": "私域分析", "media": "公域分析"}
        return "、".join(names.get(m, m) for m in self.missing) + " 的产物尚未生成"


_SAFE_NAME = re.compile(r'[<>:"/\\|?*\s]+')


def _safe_name(query: str) -> str:
    """与各角色落盘时用的文件名规范化保持一致。

    两边规则必须相同，否则按主题过滤会匹配不到任何文件。
    """
    return _SAFE_NAME.sub("_", query).strip("_")[:40] or "report"


def _latest_report(directory: Path, prefix: str, query: str) -> str:
    """取该目录下**该主题**最新的角色报告内容。

    ⚠️ 必须按主题过滤，不能只取目录里最新的一份：
    角色目录会累积多个主题的产物，不过滤的话，
    给 A 主题生成报告时会读到 B 主题的结论——而且报告照常生成、看不出异常。
    文件名格式是 `{prefix}{安全化主题}_{时间戳}.md`，用通配精确匹配。
    """
    if not directory.is_dir():
        return ""
    pattern = f"{prefix}{_safe_name(query)}_*.md"
    candidates = [p for p in directory.glob(pattern) if p.is_file()]
    if not candidates:
        return ""
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        return latest.read_text(encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        logger.warning("读取报告失败 {}：{}", latest, exc)
        return ""


def load_reports(query: str) -> tuple[str, str, str]:
    """加载指定主题的三方角色报告，返回 (host, insight, media)。"""
    from engines.contracts.config import get_settings

    settings = get_settings()
    return (
        _latest_report(Path(settings.report_dir_for_role(ROLE_HOST)),
                       _ROLE_PREFIX[ROLE_HOST], query),
        _latest_report(Path(settings.report_dir_for_role(ROLE_INSIGHT)),
                       _ROLE_PREFIX[ROLE_INSIGHT], query),
        _latest_report(Path(settings.report_dir_for_role(ROLE_MEDIA)),
                       _ROLE_PREFIX[ROLE_MEDIA], query),
    )


async def collect_stats(query: str) -> dict:
    """汇总舆情统计数据。

    报告第一段要求「全网总声量与负面率」这类数字，它们不在任何角色报告里，
    需要从分析结果表统计。

    **不能直接拿主题词去 LIKE content_uid** ——那个字段是
    `{信源类型}:{表名}:{业务ID}`，根本不含主题词，匹配结果恒为 0，
    而且返回的是「0 条」而不是报错，看上去像"这个主题真的没有数据"。

    正确做法是**先经接入层拿该主题的内容标识**（那里有双向模糊匹配），
    再用标识去查分析结果。
    """
    from sqlalchemy import func, select

    from engines.common.db.models import ContentAnalysis
    from engines.common.db.session import session_scope
    from engines.ingestion.reader import FetchQuery, fetch_contents

    stats: dict = {"query": query}
    try:
        fetched = await fetch_contents(
            FetchQuery(keyword=query, include_comments=False, limit_per_table=5000))
        uids = [c.content_uid for c in fetched.contents]
        stats["content_count"] = len(uids)
        if not uids:
            stats["total_volume"] = 0
            return stats

        # 分批查询：SQLite/MySQL 的 IN 子句参数过多会报错
        batch = 500
        labeled: list[tuple[str, str, float]] = []
        async with session_scope() as session:
            for start in range(0, len(uids), batch):
                chunk = uids[start:start + batch]
                rows = await session.execute(
                    select(ContentAnalysis.sentiment, ContentAnalysis.risk_level,
                           ContentAnalysis.intensity_index)
                    .where(ContentAnalysis.content_uid.in_(chunk)))
                labeled.extend((row[0], row[1], float(row[2] or 0)) for row in rows)

        stats["total_volume"] = len(labeled)
        if not labeled:
            return stats

        sentiment: dict[str, int] = {}
        risk_levels: dict[str, int] = {}
        for label, level, _ in labeled:
            sentiment[label] = sentiment.get(label, 0) + 1
            key = level or "C"
            risk_levels[key] = risk_levels.get(key, 0) + 1

        stats["sentiment"] = sentiment
        stats["risk_levels"] = risk_levels
        stats["negative_ratio"] = round(
            sentiment.get("negative", 0) / len(labeled), 4)
        stats["avg_intensity"] = round(
            sum(item[2] for item in labeled) / len(labeled), 4)
        stats["labeled_ratio"] = round(len(labeled) / len(uids), 4)
    except Exception as exc:  # noqa: BLE001
        logger.warning("统计汇总失败（不影响报告结构）：{}", type(exc).__name__)
        stats.setdefault("error", type(exc).__name__)
    return stats


def load_inputs(query: str) -> ReportInputs:
    """同步加载三方产物（统计数据由调用方异步补充）。"""
    host, insight, media = load_reports(query)
    inputs = ReportInputs(query=query, host_report=host,
                          insight_report=insight, media_report=media)
    if not host:
        inputs.missing.append("host")
    if not insight:
        inputs.missing.append("insight")
    if not media:
        inputs.missing.append("media")
    return inputs
