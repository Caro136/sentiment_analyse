"""公域研究员节点：规划 → 搜索 → 游标逐章生成 → 排版 → 落盘。

与私域研究员的差异只在**材料来源**：那边从自有库检索，这边从公开网络搜索。
规划、逐章生成、排版、落盘的逻辑与节奏完全一致——这样两侧产出的章节才可配对。
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
from engines.contracts.enums import EvidenceStrength
from engines.contracts.roles import ROLE_MEDIA
from engines.contracts.section import SectionDraft
from engines.media_agent.prompts import (
    FALLBACK_BODY_TEMPLATE,
    FORMAT_SYSTEM,
    FORMAT_USER_TEMPLATE,
    SEARCH_PLAN_SYSTEM,
    SEARCH_PLAN_USER_TEMPLATE,
    SUMMARY_SYSTEM,
    SUMMARY_USER_TEMPLATE,
)
from engines.media_agent.schemas import MediaSearchPlanOutput, SummaryBody
from engines.media_agent.search import SearchResult, SearchService
from engines.media_agent.state import MediaPlan, MediaState

# 每个维度取多少条搜索结果：太多会把 prompt 撑爆，且尾部结果相关性下降明显
SEARCH_COUNT_PER_KEYWORD = 4
MAX_BLOCKS_PER_SECTION = 8
MAX_CONTENT_IN_BLOCK = 220

STRENGTH_LABELS = {"strong": "充分", "medium": "一般", "weak": "不足", "none": "缺失"}

_client: LLMClient | None = None


def default_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient.from_role(ROLE_MEDIA)
    return _client


def reset_client() -> None:
    global _client
    _client = None


def render_blocks(results: list[SearchResult],
                  limit: int = MAX_BLOCKS_PER_SECTION) -> list[str]:
    """把搜索结果渲染成 Prompt 里的材料块。

    带上来源标题：公域材料的可信度差异很大（官方通报 vs 自媒体转述），
    模型需要据此判断该给这条信息多大的权重。
    """
    blocks = []
    for index, result in enumerate(results[:limit], start=1):
        content = result.content.replace("\n", " ").strip()[:MAX_CONTENT_IN_BLOCK]
        blocks.append(f"[{index}] {result.title}\n    来源：{result.url}\n    {content}")
    return blocks


# ---------------------------------------------------------------------------
# 1. 检索规划
# ---------------------------------------------------------------------------

def normalize_plan(output: MediaSearchPlanOutput) -> MediaPlan:
    """规整为固定五维的检索计划。

    模型可能漏掉某些维度，缺失的用主题词本身兜底——
    一个维度搜不到材料可以接受，但「压根没去搜」是另一回事。
    """
    given = {s.dimension_key: s for s in output.dimensions
             if s.dimension_key in DIMENSIONS}
    keywords: dict[str, list[str]] = {}
    for dimension in get_dimensions():
        spec = given.get(dimension.key)
        words = [w.strip() for w in (spec.search_keywords if spec else []) if w.strip()]
        keywords[dimension.key] = words[:3]
    return MediaPlan(overview=(output.overview or "")[:300],
                     keywords_by_dimension=keywords)


async def plan_node(state: MediaState) -> dict:
    """为五个维度规划检索词。"""
    query = state["query"]
    publish_role_progress(ROLE_MEDIA, "planning", "正在规划检索策略", 10)

    client = default_client()
    try:
        output = await client.generate_object(
            SEARCH_PLAN_USER_TEMPLATE.format(query=query),
            MediaSearchPlanOutput, system=SEARCH_PLAN_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        from engines.common.logging import mask_secret

        logger.warning("检索规划失败（{}）| {}", type(exc).__name__,
                       mask_secret(str(exc))[:200])
        output = MediaSearchPlanOutput()

    plan = normalize_plan(output)

    # 某个维度没规划出检索词时，用「主题词 + 维度关键词」兜底
    for dimension in get_dimensions():
        if not plan.keywords_by_dimension.get(dimension.key):
            fallback = f"{query} {' '.join(dimension.cluster_keywords[:2])}"
            plan.keywords_by_dimension[dimension.key] = [fallback]

    logger.info("检索规划完成：{} 个维度 / {} 个检索词",
                len(plan.keywords_by_dimension), plan.total_keywords)
    return {"plan": plan, "sections": [], "cursor": 0}


# ---------------------------------------------------------------------------
# 2. 执行检索
# ---------------------------------------------------------------------------

async def search_node(state: MediaState) -> dict:
    """按维度执行检索，结果归集到各自维度。

    按维度分别检索而不是只搜一次主题词：不同维度关心的信息完全不同
    （背景要通报、传播要热度数据、情感要评论），一次搜索喂五个章节
    必然出现材料高度重复。
    """
    plan: MediaPlan = state["plan"]
    service = SearchService()
    publish_role_progress(ROLE_MEDIA, "searching", "正在检索公开信息", 30)

    section_evidence: dict[str, list[SearchResult]] = {}
    all_results: list[SearchResult] = []
    seen: set[str] = set()

    for dimension in get_dimensions():
        keywords = plan.keywords_for(dimension.key)
        found = await service.search_many(keywords, count=SEARCH_COUNT_PER_KEYWORD)
        section_evidence[dimension.key] = found
        for result in found:
            key = result.url or result.title
            if key not in seen:
                seen.add(key)
                all_results.append(result)

    stats = {
        "provider": service.last_provider or "(未成功)",
        "available": service.available,
        "per_dimension": {k: len(v) for k, v in section_evidence.items()},
        "total": len(all_results),
    }
    logger.info("公域检索完成：{} 条（供应商 {}）", len(all_results), stats["provider"])
    publish_role_progress(ROLE_MEDIA, "searching", "检索完成", 40)

    return {"results": all_results, "section_evidence": section_evidence,
            "search_stats": stats}


# ---------------------------------------------------------------------------
# 3. 逐章生成（游标循环）
# ---------------------------------------------------------------------------

async def summarize_node(state: MediaState) -> dict:
    """生成当前游标指向的章节，并发布章节就绪事件。"""
    plan: MediaPlan = state["plan"]
    cursor = state.get("cursor", 0)
    sections = list(state.get("sections", []))

    # 章节顺序按五维固定序，与私域保持一致
    ordered_keys = [d.key for d in get_dimensions()]
    if cursor >= len(ordered_keys):
        return {"cursor": cursor}

    section_key = ordered_keys[cursor]
    title = DIMENSIONS[section_key].title
    results = state.get("section_evidence", {}).get(section_key, [])

    strength = EvidenceStrength.from_count(len(results))
    progress = 45 + int(40 * cursor / max(1, len(ordered_keys)))
    publish_role_progress(ROLE_MEDIA, "summary", f"正在生成：{title}", progress)

    if strength is EvidenceStrength.NONE:
        body = FALLBACK_BODY_TEMPLATE.format(count=len(results))
        logger.info("公域章节「{}」无材料，输出占位正文", title)
    else:
        prompt = SUMMARY_USER_TEMPLATE.format(
            title=title,
            focus_points="、".join(DIMENSIONS[section_key].cluster_keywords[:3]),
            evidence_count=len(results),
            strength=STRENGTH_LABELS.get(strength.value, strength.value),
            evidence_blocks="\n".join(render_blocks(results)) or "（无）",
        )
        client = default_client()
        try:
            output = await client.generate_object(prompt, SummaryBody,
                                                  system=SUMMARY_SYSTEM)
            body = output.body
        except Exception as exc:  # noqa: BLE001
            logger.warning("公域章节「{}」生成失败：{}", title, type(exc).__name__)
            body = FALLBACK_BODY_TEMPLATE.format(count=len(results))

    draft = SectionDraft(
        section_key=section_key, index=cursor, title=title, body=body,
        evidence_count=len(results), strength=strength.value,
        is_fallback=(strength is EvidenceStrength.NONE))
    sections.append(draft)

    publish_section_ready(
        source=ROLE_MEDIA, section_key=section_key, query=state["query"],
        body=draft.body, agent_name="公域研究员", section_index=cursor,
        title=title,
        section_metadata={"hit_count": draft.evidence_count,
                          "strength": draft.strength})

    return {"sections": sections, "cursor": cursor + 1}


def route_after_summarize(state: MediaState) -> str:
    """还有章节就继续循环，否则进入排版。"""
    return ("summarize"
            if state.get("cursor", 0) < len(get_dimensions())
            else "format")


# ---------------------------------------------------------------------------
# 4. 排版
# ---------------------------------------------------------------------------

async def format_node(state: MediaState) -> dict:
    """整理成连贯正文；失败时回退为直接拼接。"""
    sections: list[SectionDraft] = state.get("sections", [])
    drafts = "\n\n".join(s.to_markdown() for s in sections)
    publish_role_progress(ROLE_MEDIA, "formatting", "正在排版报告", 88)

    client = default_client()
    try:
        body = await client.generate_text(
            FORMAT_USER_TEMPLATE.format(query=state["query"], drafts=drafts),
            system=FORMAT_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        logger.warning("公域排版失败（{}），回退为直接拼接", type(exc).__name__)
        body = drafts
    return {"report_markdown": body}


# ---------------------------------------------------------------------------
# 5. 落盘
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\s]+')


async def persist_node(state: MediaState) -> dict:
    """把报告写入角色输出目录。"""
    from engines.contracts.config import get_settings

    settings = get_settings()
    directory = settings.report_dir_for_role(ROLE_MEDIA)
    safe = _SAFE_NAME.sub("_", state["query"]).strip("_")[:40] or "report"
    path = Path(directory) / f"media_{safe}_{int(time.time())}.md"

    stats = state.get("search_stats") or {}
    header = (f"# 公域媒体视角报告\n\n"
              f"> 研判主题：{state['query']}\n"
              f"> 搜索供应商：{stats.get('provider', '未知')}\n"
              f"> 材料条数：{len(state.get('results') or [])}\n"
              f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n")
    path.write_text(header + state.get("report_markdown", ""), encoding="utf-8")

    logger.info("公域报告已落盘：{}", path)
    publish_role_progress(ROLE_MEDIA, "completed", "公域分析完成", 100)
    return {"report_path": str(path)}
