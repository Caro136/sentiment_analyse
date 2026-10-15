"""报告引擎：把三方角色产物聚合成五段式报告。

**薄引擎的三个体现**：不订阅事件、不调用分析角色、只读磁盘。
研究阶段产出材料，本引擎负责把材料组织成正式报告，两者完全解耦——
好处是报告可以反复重生成（调模板、换模型）而不必重跑研判链。

失败时的行为要明确：材料不全就**拒绝生成并说清缺什么**，
而不是硬凑一份只有一半材料的报告（PRD FR-62）。
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from engines.common.llm_client import LLMClient
from engines.common.logging import logger, mask_secret
from engines.contracts.roles import ROLE_REPORT
from engines.report_engine.loader import ReportInputs, collect_stats, load_inputs
from engines.report_engine.prompts import REPORT_SYSTEM, REPORT_USER_TEMPLATE
from engines.report_engine.renderer import render_html

# 送入模型的材料长度上限。三个角色报告加起来可能上万字，
# 但报告引擎只做组织而非深读，超出部分按章节截断即可。
MAX_MATERIAL_LENGTH = 9000

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\s]+')
_client: LLMClient | None = None


def default_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient.from_role(ROLE_REPORT)
    return _client


def reset_client() -> None:
    global _client
    _client = None


@dataclass
class ReportOutput:
    """一次报告生成的产出。"""

    query: str = ""
    markdown: str = ""
    html: str = ""
    markdown_path: str = ""
    html_path: str = ""
    stats: dict = field(default_factory=dict)
    duration: float = 0.0
    error: str = ""
    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.markdown)

    def render(self) -> str:
        if self.error:
            return f"报告生成失败：{self.error}"
        return (f"报告已生成：{len(self.markdown)} 字，"
                f"耗时 {self.duration:.1f}s → {self.html_path}")

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "ok": self.ok,
            "error": self.error,
            "markdown_path": self.markdown_path,
            "html_path": self.html_path,
            "duration": round(self.duration, 1),
            "stats": self.stats,
        }


def _truncate(text: str, limit: int = MAX_MATERIAL_LENGTH) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n…（材料过长已截断，请以完整产物文件为准）"


def _format_stats(stats: dict) -> str:
    """把统计字典渲染成便于模型阅读的列表。"""
    if not stats:
        return "（无统计数据）"

    lines = []
    if stats.get("total_volume") is not None:
        lines.append(f"- 全网总声量：{stats['total_volume']} 条")
    if stats.get("negative_ratio") is not None:
        lines.append(f"- 负面率：{stats['negative_ratio']:.1%}")
    if stats.get("avg_intensity") is not None:
        lines.append(f"- 平均舆情烈度：{stats['avg_intensity']}")
    if stats.get("sentiment"):
        sentiment = stats["sentiment"]
        lines.append("- 情感分布：" + "、".join(
            f"{k} {v} 条" for k, v in sorted(sentiment.items())))
    if stats.get("risk_levels"):
        levels = stats["risk_levels"]
        lines.append("- 风险等级分布：" + "、".join(
            f"{level} 级 {levels.get(level, 0)} 条" for level in ("S", "A", "B", "C")))
    if stats.get("by_source"):
        lines.append("- 信源分布：" + "、".join(
            f"{k} {v} 条" for k, v in sorted(stats["by_source"].items())))
    if stats.get("error"):
        lines.append(f"- （统计汇总异常：{stats['error']}）")
    return "\n".join(lines) if lines else "（无统计数据）"


async def _compose(inputs: ReportInputs, client: LLMClient | None = None) -> str:
    """调用模型组装五段式报告。"""
    llm = client or default_client()
    prompt = REPORT_USER_TEMPLATE.format(
        query=inputs.query,
        stats=_format_stats(inputs.stats),
        host_report=_truncate(inputs.host_report),
        insight_report=_truncate(inputs.insight_report),
        media_report=_truncate(inputs.media_report),
    )
    return await llm.generate_text(prompt, system=REPORT_SYSTEM)


def _save(markdown: str, query: str, directory: Path | None = None) -> tuple[str, str]:
    """落盘 Markdown 与 HTML，返回两个路径。"""
    from engines.contracts.config import get_settings

    settings = get_settings()
    target = directory or Path(settings.report_dir_for_role(ROLE_REPORT))
    target.mkdir(parents=True, exist_ok=True)

    safe = _SAFE_NAME.sub("_", query).strip("_")[:40] or "report"
    stamp = int(time.time())
    markdown_path = target / f"report_{safe}_{stamp}.md"
    html_path = target / f"report_{safe}_{stamp}.html"

    markdown_path.write_text(markdown, encoding="utf-8")
    # HTML 里带上元信息，便于单独打开时知道这份报告是哪次生成的
    html_doc = render_html(markdown, f"舆情研判报告：{query}", meta={
        "研判主题": query,
        "生成时间": time.strftime("%Y-%m-%d %H:%M:%S"),
        "生成方式": "多智能体研判",
    })
    html_path.write_text(html_doc, encoding="utf-8")
    # 同时把渲染好的 HTML 挂回内存产物，供在线预览直接用
    return str(markdown_path), str(html_path)


async def generate_report(
    query: str,
    *,
    inputs: ReportInputs | None = None,
    client: LLMClient | None = None,
    persist: bool = True,
    output_dir: Path | None = None,
) -> ReportOutput:
    """生成一份五段式研判报告。

    材料不全时**拒绝生成**并明确说明缺什么，而不是产出一份半成品。
    """
    started = time.monotonic()
    material = inputs or load_inputs(query)

    # 统计数据由调用方补充（异步）；这里若还没有就现取
    if not material.stats:
        material.stats = await collect_stats(query)

    if not material.is_ready:
        error = material.describe_missing()
        logger.warning("报告生成被拒绝：{}", error)
        return ReportOutput(query=query, error=error, missing=material.missing,
                            stats=material.stats,
                            duration=time.monotonic() - started)

    try:
        markdown = await _compose(material, client)
    except Exception as exc:  # noqa: BLE001
        error = mask_secret(f"{type(exc).__name__}: {exc}")[:200]
        logger.warning("报告生成失败：{}", error)
        return ReportOutput(query=query, error=error, stats=material.stats,
                            duration=time.monotonic() - started)

    output = ReportOutput(query=query, markdown=markdown, stats=material.stats)

    if persist:
        try:
            markdown_path, html_path = _save(markdown, query, output_dir)
            output.markdown_path = markdown_path
            output.html_path = html_path
            # 供在线预览直接使用，避免再读一次文件
            output.html = render_html(markdown, f"舆情研判报告：{query}")
        except OSError as exc:  # noqa: BLE001
            output.error = f"报告落盘失败：{exc}"
            logger.warning(output.error)
    else:
        output.html = render_html(markdown, f"舆情研判报告：{query}")

    output.duration = time.monotonic() - started
    logger.info(output.render())
    return output
