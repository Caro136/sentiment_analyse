"""章节产出：规划 → 证据调拨 → 游标逐章生成 → 排版 → 落盘。

**为什么要「游标循环逐章生成」而不是一次性写完**：
报告是给人看的，也是给系统用的。逐章生成让每完成一章就能发布事件——
前端进度条动起来，主持人也能在两侧同维到齐时立即开始配对研判（FR-68）。
一次性生成的写法要等全文完成才有任何产出，链路全程黑箱。
"""

import re
import time
from pathlib import Path

from engines.common.eventing.publishers import (
    publish_role_progress,
    publish_section_ready,
)
from engines.common.llm_client import LLMClient
from engines.common.logging import logger
from engines.contracts.dimensions import DIMENSIONS, get_dimensions
from engines.contracts.evidence import SectionEvidencePack
from engines.contracts.roles import ROLE_INSIGHT
from engines.insight_agent.prompts import (
    FALLBACK_BODY_TEMPLATE,
    FORMAT_SYSTEM,
    FORMAT_USER_TEMPLATE,
    PLAN_SYSTEM,
    PLAN_USER_TEMPLATE,
    SUMMARY_SYSTEM,
    SUMMARY_USER_TEMPLATE,
)
from engines.insight_agent.schemas import ResearchPlanOutput, SectionBody
from engines.insight_agent.state import InsightState, ResearchPlan, SectionDraft

PLAN_PREVIEW_COUNT = 12
STRENGTH_LABELS = {"strong": "充分", "medium": "一般", "weak": "不足", "none": "缺失"}

_client: LLMClient | None = None


def default_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient.from_role(ROLE_INSIGHT)
    return _client


def reset_client() -> None:
    global _client
    _client = None


# ---------------------------------------------------------------------------
# 1. 章节规划
# ---------------------------------------------------------------------------

def normalize_plan(output: ResearchPlanOutput) -> ResearchPlan:
    """把模型输出规整为「固定五维、顺序正确、不缺不重」的章节计划。

    模型给的章节顺序不可信，也可能漏掉某些维度。以固定五维为绝对基准重排：
    两侧研究员（私域与公域）必须产出结构一致的章节，否则主持人无法按维度配对。
    """
    given = {s.section_key: s for s in output.sections if s.section_key in DIMENSIONS}
    sections: list[tuple[str, str, list[str]]] = []
    for dimension in get_dimensions():
        spec = given.get(dimension.key)
        title = (spec.title if spec and spec.title else dimension.title)[:40]
        focus = [point[:30] for point in (spec.focus_points if spec else [])][:3]
        sections.append((dimension.key, title, focus))
    return ResearchPlan(overview=(output.overview or "")[:300], sections=sections)


async def plan_node(state: InsightState) -> dict:
    """规划五个维度的分析要点。

    模型只负责「每个维度该回答什么」，章节的顺序与完整性由代码按五维基准规整——
    让模型自由决定章节结构，两侧（私域与公域）必然对不齐，主持人配对也就无从谈起。
    """
    pool = state["pool"]
    query = state["query"]
    publish_role_progress(ROLE_INSIGHT, "planning", "正在规划分析维度", 15)

    by_platform: dict[str, int] = {}
    by_sentiment: dict[str, int] = {}
    for record in pool.records:
        key = record.source_type.label if hasattr(record.source_type, "label") else str(record.source_type)
        by_platform[key] = by_platform.get(key, 0) + 1
        if record.sentiment:
            by_sentiment[record.sentiment] = by_sentiment.get(record.sentiment, 0) + 1

    preview = "\n".join(
        f"  - [{r.source_type.label if hasattr(r.source_type, 'label') else r.source_type}]"
        f" {r.summary(90)}"
        for r in pool.records[:PLAN_PREVIEW_COUNT])

    prompt = PLAN_USER_TEMPLATE.format(
        query=query,
        total=len(pool.records),
        platform_distribution="、".join(f"{k} {v} 条" for k, v in by_platform.items()) or "无",
        sentiment_distribution="、".join(f"{k} {v} 条" for k, v in by_sentiment.items()) or "无标签",
        evidence_preview=preview or "（无材料）",
    )

    client = default_client()
    try:
        output = await client.generate_object(prompt, ResearchPlanOutput,
                                              system=PLAN_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        from engines.common.logging import mask_secret

        logger.warning("章节规划失败（{}），回退为固定大纲 | {}",
                       type(exc).__name__, mask_secret(str(exc))[:220])
        output = ResearchPlanOutput(overview="规划失败，按固定五维大纲生成",
                                    sections=[])

    plan = normalize_plan(output)
    logger.info("章节规划完成：{} 章 | {}", len(plan.sections), plan.overview[:60])
    publish_role_progress(ROLE_INSIGHT, "planning", "规划完成，开始生成章节", 20)
    return {"plan": plan, "sections": [], "cursor": 0}


# ---------------------------------------------------------------------------
# 2. 证据调拨
# ---------------------------------------------------------------------------

async def allocation_node(state: InsightState) -> dict:
    """按维度分配证据。

    聚类阶段已经把每个簇路由到了某个维度（`cluster_{dimension_key}`），
    这里直接按约定取即可，不必再算一次相似度。
    """
    pool = state["pool"]
    allocation: dict[str, list] = {}
    for dimension in get_dimensions():
        allocation[dimension.key] = pool.by_dimension(dimension.key)

    summary = {k: len(v) for k, v in allocation.items()}
    logger.info("证据调拨完成：{}", summary)
    return {"section_evidence": allocation}


# ---------------------------------------------------------------------------
# 3. 逐章生成（游标循环）
# ---------------------------------------------------------------------------

async def summarize_node(state: InsightState) -> dict:
    """生成当前游标指向的章节，并发布章节就绪事件。"""
    plan: ResearchPlan = state["plan"]
    cursor = state.get("cursor", 0)
    sections = list(state.get("sections", []))

    if cursor >= len(plan.sections):
        return {"cursor": cursor}

    section_key, title, focus = plan.sections[cursor]
    records = state.get("section_evidence", {}).get(section_key, [])
    pool = state["pool"]
    pack = SectionEvidencePack.from_records(section_key, state["query"], records)

    progress = 40 + int(40 * cursor / max(1, len(plan.sections)))
    publish_role_progress(ROLE_INSIGHT, "summary",
                          f"正在生成：{title}", progress)

    if pack.is_empty:
        # 证据不足就直说，不编造结论
        draft = SectionDraft(
            section_key=section_key, index=cursor, title=title,
            body=FALLBACK_BODY_TEMPLATE.format(count=pack.evidence_count),
            evidence_count=0, strength="none", is_fallback=True)
        logger.info("章节「{}」证据不足，输出占位正文", title)
    else:
        prompt = SUMMARY_USER_TEMPLATE.format(
            title=title,
            focus_points="、".join(focus) if focus else "（未指定）",
            evidence_count=pack.evidence_count,
            strength=STRENGTH_LABELS.get(pack.strength.value, pack.strength.value),
            evidence_blocks="\n".join(pack.evidence_source_blocks) or "（无）",
        )
        client = default_client()
        try:
            output = await client.generate_object(prompt, SectionBody,
                                                  system=SUMMARY_SYSTEM)
            body = output.body
        except Exception as exc:  # noqa: BLE001
            logger.warning("章节「{}」生成失败：{}", title, type(exc).__name__)
            body = FALLBACK_BODY_TEMPLATE.format(count=pack.evidence_count)

        draft = SectionDraft(
            section_key=section_key, index=cursor, title=title, body=body,
            evidence_count=pack.evidence_count, strength=pack.strength.value)

    sections.append(draft)

    # 章节级事件：主持人与前端都靠它驱动（FR-68 / FR-69）
    publish_section_ready(
        source=ROLE_INSIGHT,
        section_key=section_key,
        query=pool.query,
        body=draft.body,
        agent_name="私域研究员",
        section_index=cursor,
        title=title,
        section_metadata={"hit_count": draft.evidence_count,
                          "strength": draft.strength},
    )

    return {"sections": sections, "cursor": cursor + 1}


def route_after_summarize(state: InsightState) -> str:
    """条件路由：还有章节就继续循环，否则进入排版。"""
    plan = state.get("plan")
    if plan is None:
        return "format"
    return "summarize" if state.get("cursor", 0) < len(plan.sections) else "format"


# ---------------------------------------------------------------------------
# 4. 排版
# ---------------------------------------------------------------------------

async def format_node(state: InsightState) -> dict:
    """把分章节草稿整理成连贯正文。

    排版失败时回退为直接拼接——章节内容本身就是完整的，
    少了过渡句不影响可用性，不该因此丢掉整份报告。
    """
    sections: list[SectionDraft] = state.get("sections", [])
    query = state["query"]
    drafts = "\n\n".join(s.to_markdown() for s in sections)
    publish_role_progress(ROLE_INSIGHT, "formatting", "正在排版报告", 85)

    client = default_client()
    try:
        body = await client.generate_text(
            FORMAT_USER_TEMPLATE.format(query=query, drafts=drafts),
            system=FORMAT_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        logger.warning("排版失败（{}），回退为直接拼接", type(exc).__name__)
        body = drafts

    return {"report_markdown": body}


# ---------------------------------------------------------------------------
# 5. 落盘
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\s]+')


def _safe_filename(text: str, limit: int = 40) -> str:
    return _SAFE_NAME.sub("_", text).strip("_")[:limit] or "report"


async def persist_node(state: InsightState) -> dict:
    """把报告写入角色输出目录。"""
    from engines.contracts.config import get_settings

    settings = get_settings()
    directory = settings.report_dir_for_role(ROLE_INSIGHT)
    filename = f"insight_{_safe_filename(state['query'])}_{int(time.time())}.md"
    path = Path(directory) / filename

    header = (f"# 私域舆情分析报告\n\n"
              f"> 研判主题：{state['query']}\n"
              f"> 证据条数：{len(state.get('pool').records) if state.get('pool') else 0}\n"
              f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n")
    path.write_text(header + state.get("report_markdown", ""), encoding="utf-8")

    logger.info("私域报告已落盘：{}", path)
    publish_role_progress(ROLE_INSIGHT, "completed", "私域分析完成", 100)
    return {"report_path": str(path)}
