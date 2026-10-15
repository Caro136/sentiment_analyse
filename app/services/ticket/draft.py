"""公关回应草案生成（FR-51 / PRD 附录 B.5）。

产出**三段式**：事实澄清 / 情绪疏导 / 行动方案。三段缺一不可——
只澄清事实像在推责，只疏导情绪像在敷衍，只给方案则没有回应公众最关心的疑问。

证据来自**已有分析结果**（事件内容的标签与原文片段、观点聚类、事件统计），
不重新跑一遍分析：口径必须与大盘、报告一致（ADR-02 的同一条纪律）。

**草案不自动对外**：生成结果只是「建议」，需要人工编辑后采纳（`draft_adopt`）。
模型可能写出与事实不符的承诺，直接对外发布是不可接受的风险。
"""

from dataclasses import dataclass, field

from loguru import logger
from pydantic import BaseModel, Field

from app.errors import AppError, invalid_request
from app.services.content.repository import AnalysisRecord, ContentRepository
from app.services.event.repository import EventRepository
from engines.common.llm_client import LLMClient, LLMNotConfiguredError
from engines.contracts.enums import RiskLevel, Sentiment
from engines.contracts.roles import ROLE_REPORT
from engines.ingestion.reader import load_contents

__all__ = [
    "ResponseDraft",
    "DraftContext",
    "build_context",
    "generate_draft",
    "MAX_EVIDENCE_ITEMS",
]

# 送给模型的证据条数上限：太多会冲淡重点，也会推高成本与延迟
MAX_EVIDENCE_ITEMS = 12
# 单条证据截断长度
EVIDENCE_TEXT_LIMIT = 120


class ResponseDraft(BaseModel):
    """三段式草案。字段命名与 `ticket_log` 的动作值一致（draft_adopt）。"""

    fact_clarification: str = Field(description="事实澄清：已知事实与不实信息的区分")
    emotion_guidance: str = Field(description="情绪疏导：对公众关切的共情与安抚")
    action_plan: str = Field(description="行动方案：已采取与将要采取的措施")
    caution: str = Field(default="", description="发布前需要人工确认的事项")

    def render(self) -> str:
        """拼成可直接落库/编辑的文本。"""
        parts = [
            "【事实澄清】", self.fact_clarification.strip(), "",
            "【情绪疏导】", self.emotion_guidance.strip(), "",
            "【行动方案】", self.action_plan.strip(),
        ]
        if self.caution.strip():
            parts += ["", "【发布前请确认】", self.caution.strip()]
        return "\n".join(parts)


@dataclass
class DraftContext:
    """生成草案所需的上下文（纯数据，便于单独测试拼装逻辑）。"""

    event_id: str
    title: str
    risk_level: str | None = None
    total_volume: int = 0
    negative_ratio: float | None = None
    first_seen_at: int | None = None
    sentiment_distribution: dict[str, int] = field(default_factory=dict)
    cluster_labels: list[str] = field(default_factory=list)
    sensitive_hits: list[str] = field(default_factory=list)
    high_risk_quotes: list[str] = field(default_factory=list)
    official_statements: list[str] = field(default_factory=list)


SYSTEM = ("你是资深企业公关负责人，措辞克制、对公众关切保持共情，"
          "只依据给定材料陈述，不编造事实与数据。")

PROMPT = """你是企业公关负责人，正在为一起舆情事件起草对外回应。

已知事件材料：
- 事件：{title}
- 风险等级：{risk_level}｜总声量：{total_volume}｜负面占比：{negative_ratio}
- 情感分布：{sentiment}
- 公众关注焦点（观点聚类）：{clusters}
- 高危敏感表述：{sensitive}
- 高风险的公众原话摘录：
{quotes}
- 事件相关方的既有表态：
{official}

请输出三段内容：
1. fact_clarification 事实澄清：区分「已确认的事实」与「尚在核实的信息」，
   对明显不实的说法给出纠正；**不要编造任何未经材料支持的事实或数据**。
2. emotion_guidance 情绪疏导：针对公众的核心担忧表达共情与重视，语气诚恳、不居高临下。
3. action_plan 行动方案：已经采取的措施与接下来的具体安排（含时间点或责任方更好）。
另外在 caution 字段列出「发布前必须人工确认的事项」（例如需要法务审核的表述、
需要核实的数据）。材料不足以支撑某一段时，如实写「材料不足，建议XXX后再表态」，
不要用流畅的空话填充。"""


async def build_context(repo: EventRepository, content_repo: ContentRepository, session,
                        event_id: str, *, cluster_labels: list[str] | None = None,
                        ) -> DraftContext:
    """从事件的内容标签与聚类里收集生成草案所需的事实材料。"""
    event = await repo.get_event(event_id)
    if event is None:
        from app.errors import not_found

        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    uid_map = await repo.content_uids([event_id])
    uids = uid_map.get(event_id, [])
    contents = await load_contents(uids, session) if uids else {}
    records = await content_repo.query_analysis(list(contents.keys())) if contents else []

    negative = [r for r in records if r.sentiment == Sentiment.NEGATIVE.value]
    high_risk = [r for r in records if r.risk_level in (RiskLevel.S.value, RiskLevel.A.value)]
    sensitive_words: set[str] = set()

    context = DraftContext(
        event_id=event_id,
        title=event.title,
        risk_level=_risk_of(records),
        total_volume=sum(contents[r.content_uid].engagement.total
                         for r in records if r.content_uid in contents),
        negative_ratio=(round(len(negative) / len(records), 4) if records else None),
        cluster_labels=list(cluster_labels or []),
    )

    published = [contents[r.content_uid].published_at for r in records
                 if r.content_uid in contents and contents[r.content_uid].published_at]
    context.first_seen_at = min(published) if published else None

    distribution: dict[str, int] = {}
    for record in records:
        distribution[record.sentiment] = distribution.get(record.sentiment, 0) + 1
    context.sentiment_distribution = distribution

    # 高风险内容的原话：按风险与互动量取最有代表性的若干条
    high_risk.sort(key=lambda r: (RiskLevel(r.risk_level or "C").severity,
                                  contents[r.content_uid].engagement.total
                                  if r.content_uid in contents else 0), reverse=True)
    for record in high_risk[:MAX_EVIDENCE_ITEMS]:
        content = contents.get(record.content_uid)
        if content is None:
            continue
        text = (content.raw_text or content.display_text or "").strip().replace("\n", " ")
        if text:
            context.high_risk_quotes.append(f"[{record.risk_level}] {text[:EVIDENCE_TEXT_LIMIT]}")

    for record in records:
        content = contents.get(record.content_uid)
        if content is None:
            continue
        if content.author.verified_type in ("官方", "政务"):
            text = (content.raw_text or content.display_text or "").strip()
            if text:
                context.official_statements.append(
                    f"{content.author.nickname}：{text[:EVIDENCE_TEXT_LIMIT]}")
        if record.risk_factors:
            sensitive_words.update(_sensitive_from(record))

    context.official_statements = context.official_statements[:5]
    context.sensitive_hits = sorted(sensitive_words)[:10]
    return context


def ensure_generatable(context: DraftContext) -> None:
    """对外暴露的材料门槛检查（服务层在调用模型前先过一遍）。"""
    _ensure_enough_evidence(context)


MIN_EVIDENCE_ITEMS = 1


def _ensure_enough_evidence(context: DraftContext) -> None:
    """没有材料就拒绝生成（BIZ_410）——**不产出编出来的草案**。

    与报告引擎同一条纪律（FR-62）：材料不全时说清缺什么，比硬凑一份半成品安全得多。
    事件没有任何关联内容时（例如主题建了但还没接入数据），模型只能靠空话填充，
    而一份「看起来很正式」的空话草稿极可能被直接采用——那比没有草案危险得多。
    """
    from app.errors import AppError

    if (len(context.high_risk_quotes) < MIN_EVIDENCE_ITEMS
            and context.total_volume == 0
            and not context.cluster_labels):
        raise AppError("BIZ_410",
                       "事件尚无可用材料，无法生成回应草案（请先完成该事件的接入与打标）",
                       detail=f"event_id={context.event_id}")


def _risk_of(records: list[AnalysisRecord]) -> str | None:
    severity = 0
    for record in records:
        if record.risk_level:
            severity = max(severity, RiskLevel(record.risk_level).severity)
    for level in RiskLevel:
        if level.severity == severity:
            return level.value
    return None


def _sensitive_from(record: AnalysisRecord) -> list[str]:
    """从标签的 `risk_factors` 里取风险判定提到的关键表述（尽力而为，解析失败即忽略）。"""
    import json

    try:
        factors = json.loads(record.risk_factors or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(factors, dict) or not factors.get("sensitive_hit"):
        return []
    reason = str(factors.get("reason", ""))
    return [reason[:60]] if reason else []


async def generate_draft(context: DraftContext, *, client=None) -> ResponseDraft:
    """调用模型生成草案。模型不可用时**明确失败**，不返回占位文本。

    用的是**报告引擎这个角色**的模型配置（撰写类任务，追求成稿质量）：
    不为公关草案单开一套 `PR_*` 密钥——那会多出一份必须填的配置，
    而任务性质与报告成稿几乎一致。
    """
    prompt = PROMPT.format(
        title=context.title,
        risk_level=context.risk_level or "未判定",
        total_volume=context.total_volume,
        negative_ratio=(f"{context.negative_ratio * 100:.1f}%"
                        if context.negative_ratio is not None else "未知"),
        sentiment=context.sentiment_distribution or "未知",
        clusters="、".join(context.cluster_labels) or "（暂无聚类结果）",
        sensitive="、".join(context.sensitive_hits) or "（无）",
        quotes="\n".join(f"  · {q}" for q in context.high_risk_quotes) or "  （无）",
        official="\n".join(f"  · {s}" for s in context.official_statements) or "  （无）",
    )

    llm = client or LLMClient.from_role(ROLE_REPORT)
    try:
        draft = await llm.generate_object(prompt, ResponseDraft, system=SYSTEM)
    except LLMNotConfiguredError as exc:
        # 配置缺失是「调用不了」，与「调用失败」要给出不同的提示：
        # 前者要人去填 .env，后者是稍后重试
        raise AppError("ANA_301", "回应草案生成失败：报告引擎模型未配置",
                       detail="REPORT_ENGINE_API_KEY / REPORT_ENGINE_MODEL_NAME") from exc
    except Exception as exc:  # noqa: BLE001 —— 客户端已内置重试，到这里的都算失败
        logger.opt(exception=exc).error("回应草案生成失败 event_id={}", context.event_id)
        raise AppError("ANA_301", f"回应草案生成失败：{type(exc).__name__}") from exc

    if not isinstance(draft, ResponseDraft):
        raise invalid_request("回应草案生成结果格式不符")

    logger.info("回应草案已生成 event_id={} 风险={}", context.event_id, context.risk_level)
    return draft
