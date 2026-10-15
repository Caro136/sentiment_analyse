"""主持人的图节点。

    解析章节 → 累积配对 → 找出就绪维度 →(有就绪) 逐维研判 → 记录
                                              ↓(五维完成)
                                        最终裁判 → 落盘

两个条件出口：
  - 找出就绪维度后：没有就绪的（等另一侧）就直接结束本次调用；
  - 记录研判后：五维未完成就结束，完成了才生成最终裁判。
"""

import re
import time
from pathlib import Path

from engines.common.eventing.publishers import (
    publish_host_message,
    publish_role_progress,
)
from engines.common.llm_client import LLMClient
from engines.common.logging import logger
from engines.contracts.dimensions import DIMENSIONS, get_dimensions
from engines.contracts.roles import ROLE_HOST
from engines.contracts.section import SectionDraft
from engines.host_agent.prompts import (
    FINAL_SYSTEM,
    FINAL_USER_TEMPLATE,
    JUDGE_SYSTEM,
    JUDGE_USER_TEMPLATE,
)
from engines.host_agent.schemas import FinalReportOutput, SectionJudgementOutput
from engines.host_agent.state import HostState, SectionJudgement

_client: LLMClient | None = None


def default_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient.from_role(ROLE_HOST)
    return _client


def reset_client() -> None:
    global _client
    _client = None


# ---------------------------------------------------------------------------
# 1. 解析事件
# ---------------------------------------------------------------------------

async def parse_section(state: HostState) -> dict:
    """把事件载荷还原成章节草稿。

    事件里带的是扁平字段（Pydantic 事件模型不好嵌 dataclass），
    这里把它还原成 SectionDraft。
    """
    section_key = state.get("incoming_section_key", "")
    if not section_key:
        return {}
    return {
        "incoming_body": state.get("incoming_body", ""),
        "ready_keys": [],
    }


# ---------------------------------------------------------------------------
# 2. 累积到配对存储
# ---------------------------------------------------------------------------

async def accumulate_section(state: HostState) -> dict:
    """把本次到达的章节登记进 PairStore。"""
    pairs = state.get("pairs")
    section_key = state.get("incoming_section_key", "")
    source = state.get("source", "")
    if pairs is None or not section_key or not source:
        return {}

    draft = SectionDraft(
        section_key=section_key,
        index=0,
        title=state.get("incoming_title", "") or DIMENSIONS.get(
            section_key, get_dimensions()[0]).title,
        body=state.get("incoming_body", ""),
        strength=state.get("incoming_strength", ""),
        evidence_count=int(state.get("incoming_evidence_count", 0) or 0),
    )
    if pairs.add(source, draft):
        missing = pairs.missing_sources(section_key)
        logger.info("主持人收到章节 {}/{}，该维度还缺 {}",
                    section_key, source, missing or "无")
    return {}


# ---------------------------------------------------------------------------
# 3. 找出就绪的维度
# ---------------------------------------------------------------------------

async def find_ready_pairs(state: HostState) -> dict:
    """找出两侧章节都已到齐、且尚未研判的维度。"""
    pairs = state.get("pairs")
    return {"ready_keys": pairs.ready_keys() if pairs else []}


def route_after_find(state: HostState) -> str:
    """有就绪维度就去研判，否则结束本次调用（继续等另一侧）。"""
    return "judge" if state.get("ready_keys") else "end"


# ---------------------------------------------------------------------------
# 4. 逐维研判（LLM）
# ---------------------------------------------------------------------------

async def judge_section(state: HostState) -> dict:
    """对每个就绪维度做一次研判。

    一次调用可能让多个维度同时就绪（两侧章节几乎同时到达），
    这里逐个研判，每个维度独立调用模型——合并成一次调用虽然省 token，
    但会让模型在多个维度之间串味，且任一维度出错会拖垮整批。
    """
    pairs = state.get("pairs")
    ready_keys: list[str] = list(state.get("ready_keys") or [])
    query = state.get("query", "")
    client = default_client()
    judgements = list(state.get("judgements") or [])

    for index, section_key in enumerate(ready_keys):
        bucket = pairs.pair_of(section_key) if pairs else {}
        insight = bucket.get("insight")
        media = bucket.get("media")
        title = DIMENSIONS.get(section_key).title if section_key in DIMENSIONS else section_key

        publish_role_progress(ROLE_HOST, "judging", f"正在研判：{title}",
                              50 + int(40 * index / max(1, len(ready_keys))))

        prompt = JUDGE_USER_TEMPLATE.format(
            query=query,
            title=title,
            insight_strength=getattr(insight, "strength", "") or "未知",
            insight_count=getattr(insight, "evidence_count", 0),
            insight_body=_truncate(getattr(insight, "body", "") or "（本文来源未产出内容）"),
            media_strength=getattr(media, "strength", "") or "未知",
            media_count=getattr(media, "evidence_count", 0),
            media_body=_truncate(getattr(media, "body", "") or "（本文来源未产出内容）"),
        )

        try:
            output = await client.generate_object(prompt, SectionJudgementOutput,
                                                  system=JUDGE_SYSTEM)
        except Exception as exc:  # noqa: BLE001
            logger.warning("维度 {} 研判失败：{}", section_key, type(exc).__name__)
            output = SectionJudgementOutput(summary="本维度研判失败，请人工复核")

        judgements.append(SectionJudgement(
            section_key=section_key,
            title=title,
            consensus=list(output.consensus),
            divergence=list(output.divergence),
            risks=list(output.risks),
            summary=output.summary,
            insight_strength=getattr(insight, "strength", "") or "",
            media_strength=getattr(media, "strength", "") or "",
        ))

    return {"judgements": judgements}


def _truncate(text: str, limit: int = 2500) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…（已截断）"


# ---------------------------------------------------------------------------
# 5. 记录研判
# ---------------------------------------------------------------------------

async def record_judgement(state: HostState) -> dict:
    """标记已研判，并把结论作为讨论消息推出去。"""
    pairs = state.get("pairs")
    judgements = list(state.get("judgements") or [])
    query = state.get("query", "")

    for judgement in judgements:
        if pairs and judgement.section_key not in pairs.judged:
            pairs.mark_judged(judgement.section_key)
            publish_host_message(
                judgement.summary or "（本维度无结论）",
                section_key=judgement.section_key,
                section_title=judgement.title,
                query=query,
            )

    logger.info("主持人会话进度：{}", pairs.stats() if pairs else {})
    return {}


def route_after_record(state: HostState) -> str:
    """五维全部研判完成才生成最终裁判，否则结束本次调用。"""
    pairs = state.get("pairs")
    return "final" if pairs and pairs.all_done() else "end"


# ---------------------------------------------------------------------------
# 6. 最终裁判（LLM）
# ---------------------------------------------------------------------------

async def generate_final_report(state: HostState) -> dict:
    """汇总五维研判，产出最终裁判结论。"""
    query = state.get("query", "")
    judgements = list(state.get("judgements") or [])
    publish_role_progress(ROLE_HOST, "finalizing", "正在形成最终裁判结论", 92)

    judgements_text = "\n\n".join(j.to_markdown() for j in judgements) or "（无研判结果）"
    client = default_client()

    try:
        output = await client.generate_object(
            FINAL_USER_TEMPLATE.format(query=query, judgements=judgements_text),
            FinalReportOutput, system=FINAL_SYSTEM)
    except Exception as exc:  # noqa: BLE001
        logger.warning("最终裁判生成失败：{}", type(exc).__name__)
        return {"final_report": "", "final_confidence": 0.0}

    report = _render_final_report(query, output)
    publish_host_message(report, query=query, is_final=True)
    logger.info("最终裁判已生成（置信度 {}）", output.confidence)
    return {"final_report": report, "final_confidence": output.confidence}


def _render_final_report(query: str, output: FinalReportOutput) -> str:
    lines = [f"# 综合研判结论：{query}", "", output.conclusion or "（无结论）", ""]
    if output.key_risks:
        lines.append("## 优先风险")
        lines.extend(f"- {item}" for item in output.key_risks)
        lines.append("")
    if output.recommendations:
        lines.append("## 行动建议")
        lines.extend(f"- {item}" for item in output.recommendations)
        lines.append("")
    lines.append(f"> 研判置信度：{output.confidence:.0%}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. 落盘
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r'[<>:"/\\|?*\s]+')


async def save_report(state: HostState) -> dict:
    """把五维研判与最终裁判写入角色输出目录。"""
    from engines.contracts.config import get_settings

    settings = get_settings()
    directory = settings.report_dir_for_role(ROLE_HOST)
    query = state.get("query", "report")
    safe = _SAFE_NAME.sub("_", query).strip("_")[:40] or "report"
    path = Path(directory) / f"host_{safe}_{int(time.time())}.md"

    judgements = list(state.get("judgements") or [])
    order = {d.key: i for i, d in enumerate(get_dimensions())}
    ordered = sorted(judgements, key=lambda j: order.get(j.section_key, 99))

    parts = [state.get("final_report", "")]
    if ordered:
        parts.append("\n---\n\n## 分维度研判明细\n")
        parts.extend(j.to_markdown() for j in ordered)

    path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("主持人研判报告已落盘：{}", path)
    publish_role_progress(ROLE_HOST, "completed", "综合研判完成", 100)
    return {"report_path": str(path)}
