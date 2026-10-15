"""认知分析编排：批量打标 → 烈度与风险 → 落库。

三个关键行为：

1. **失败逐条兜底，不中断整批**。一条内容的模型调用失败，其余照常分析；
   失败的那条标成零置信度中性并计入 `failed_uids`，供事后重跑。
2. **幂等**。按 `content_uid` 写入，重跑不会产生重复记录；已人工复核的结论不被覆盖（FR-24）。
3. **已打标的可跳过**。批量重跑时只处理新增内容，省下重复的模型调用。
"""

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from engines.analytics import repository
from engines.analytics.intensity import IntensityInput, compute_intensity
from engines.analytics.sentiment import (
    default_client,
    fallback_judgement,
    judge_content,
)
from engines.common.db.session import session_scope
from engines.common.logging import logger, mask_secret
from engines.common.runtime.concurrency import DEFAULT_LIMIT, gather_limited
from engines.contracts.config import get_settings
from engines.contracts.content import AnalysisLabel, UnifiedContent
from engines.contracts.enums import Sentiment

# 模型版本标识：写入分析结果，便于在模型/Prompt 调整后识别历史标签的生成来源
MODEL_VERSION = "analytics-v1"


@dataclass
class AnalysisStats:
    """打标统计。"""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    sentiment: dict[str, int] = field(default_factory=dict)
    risk_level: dict[str, int] = field(default_factory=dict)
    emotion: dict[str, int] = field(default_factory=dict)
    sensitive_hits: int = 0
    avg_intensity: float = 0.0

    def render(self) -> str:
        analyzed = self.succeeded + self.failed
        sentiment_text = "，".join(
            f"{k} {v}" for k, v in sorted(self.sentiment.items(), key=lambda kv: -kv[1]))
        risk_text = "，".join(
            f"{k} 级 {self.risk_level.get(k, 0)}" for k in ("S", "A", "B", "C"))
        return (
            f"打标 {analyzed} 条（成功 {self.succeeded} / 失败 {self.failed}，跳过 {self.skipped}）| "
            f"情感：{sentiment_text} | 风险：{risk_text} | "
            f"平均烈度 {self.avg_intensity:.3f} | 敏感命中 {self.sensitive_hits}"
        )


@dataclass
class AnalysisResult:
    """打标产出。"""

    labels: dict[str, AnalysisLabel] = field(default_factory=dict)
    stats: AnalysisStats = field(default_factory=AnalysisStats)
    failed_uids: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.labels)


def _to_payload(content: UnifiedContent, label: AnalysisLabel, *,
                fingerprint: int | None, is_bot: bool, analyzed_at: int,
                topic_id: str | None = None) -> dict:
    """构造落库数据。列名与 content_analysis 表一一对应。"""
    return {
        "content_uid": content.content_uid,
        "source_type": content.source_type.value,
        "source_table": content.source_table,
        "business_id": content.business_id,
        "topic_id": topic_id,
        "sentiment": label.sentiment,
        "emotion": label.emotion or None,
        "sentiment_confidence": label.sentiment_confidence,
        "intensity_index": label.intensity_index,
        "risk_level": label.risk_level,
        "risk_factors": json.dumps(label.risk_factors, ensure_ascii=False),
        "simhash": str(fingerprint) if fingerprint else None,
        "is_suspected_bot": is_bot,
        "model_version": label.model_version,
        "analyzed_at": analyzed_at,
    }


async def _analyze(contents: Sequence[UnifiedContent], session: AsyncSession | None, *,
                   fingerprints: dict[str, int] | None,
                   bot_account_ids: set[str] | None,
                   max_concurrency: int, skip_analyzed: bool,
                   topic_id: str | None = None,
                   client=None) -> AnalysisResult:
    settings = get_settings()
    weights = settings.intensity_weights
    fingerprints = fingerprints or {}
    bot_account_ids = bot_account_ids or set()

    stats = AnalysisStats(total=len(contents))
    targets = list(contents)

    if session is not None and skip_analyzed:
        already = await repository.load_analyzed_uids(
            session, [c.content_uid for c in targets])
        if already:
            targets = [c for c in targets if c.content_uid not in already]
            stats.skipped = len(contents) - len(targets)

    if not targets:
        logger.info("没有需要打标的内容（全部已分析或输入为空）")
        return AnalysisResult(stats=stats)

    llm = client or default_client()
    outcomes = await gather_limited(
        [judge_content(content, llm) for content in targets],
        limit=max_concurrency)

    labels: dict[str, AnalysisLabel] = {}
    payloads: list[dict] = []
    failed_uids: list[str] = []
    analyzed_at = int(time.time())
    intensity_sum = 0.0

    for content, outcome in zip(targets, outcomes):
        if isinstance(outcome, BaseException):
            failed_uids.append(content.content_uid)
            stats.failed += 1
            logger.warning("打标失败 uid={} err={}", content.content_uid,
                           mask_secret(f"{type(outcome).__name__}: {outcome}")[:160])
            judgement = fallback_judgement(f"{type(outcome).__name__}")
        else:
            stats.succeeded += 1
            judgement = outcome

        is_bot = content.author.account_id in bot_account_ids
        intensity = compute_intensity(
            IntensityInput(
                sentiment=judgement.sentiment,
                confidence=judgement.confidence,
                engagement_total=content.engagement.total,
                influence_score=content.author.influence_score,
                sensitive_hit=judgement.sensitive_hit,
                is_suspected_bot=is_bot,
            ),
            weights)

        label = AnalysisLabel(
            sentiment=judgement.sentiment,
            emotion=judgement.emotion,
            sentiment_confidence=judgement.confidence,
            intensity_index=intensity.index,
            risk_level=intensity.risk_level.value,
            risk_factors={**intensity.factors,
                          "sensitive_hit": judgement.sensitive_hit,
                          "reason": judgement.reason},
            model_version=MODEL_VERSION,
        )
        labels[content.content_uid] = label
        intensity_sum += intensity.index

        stats.sentiment[judgement.sentiment] = stats.sentiment.get(judgement.sentiment, 0) + 1
        stats.risk_level[label.risk_level] = stats.risk_level.get(label.risk_level, 0) + 1
        if judgement.emotion:
            stats.emotion[judgement.emotion] = stats.emotion.get(judgement.emotion, 0) + 1
        if judgement.sensitive_hit:
            stats.sensitive_hits += 1

        payloads.append(_to_payload(
            content, label,
            fingerprint=fingerprints.get(content.content_uid),
            is_bot=is_bot, analyzed_at=analyzed_at,
            topic_id=topic_id))

    if labels:
        stats.avg_intensity = round(intensity_sum / len(labels), 4)

    if session is not None and payloads:
        written = await repository.save_labels(session, payloads)
        logger.info("打标结果已落库 {} 条", written)

    return AnalysisResult(labels=labels, stats=stats, failed_uids=failed_uids)


async def analyze_contents(
    contents: Sequence[UnifiedContent],
    *,
    session: AsyncSession | None = None,
    fingerprints: dict[str, int] | None = None,
    bot_account_ids: set[str] | None = None,
    max_concurrency: int = DEFAULT_LIMIT,
    skip_analyzed: bool = True,
    topic_id: str | None = None,
    client=None,
    persist: bool = True,
) -> AnalysisResult:
    """批量打标。

    `fingerprints` 与 `bot_account_ids` 来自接入流程（去重与水军识别结果），
    传入后一并写入分析表——它们是多模态与风险判定的输入。
    `topic_id` 是**这一批内容归属的监测主题**：写进标签表，并随事件传给预警引擎
    （不传也能跑，但主题筛选与按主题的规则评估会失去依据）。
    `client` 用于注入模型客户端（测试替身或自定义配置）。
    `persist=False` 时只分析不落库，用于试跑与单测。
    """
    if not persist:
        return await _analyze(contents, None,
                              fingerprints=fingerprints,
                              bot_account_ids=bot_account_ids,
                              max_concurrency=max_concurrency,
                              skip_analyzed=False,
                              topic_id=topic_id,
                              client=client)

    if session is not None:
        result = await _analyze(contents, session,
                                fingerprints=fingerprints,
                                bot_account_ids=bot_account_ids,
                                max_concurrency=max_concurrency,
                                skip_analyzed=skip_analyzed,
                                topic_id=topic_id,
                                client=client)
    else:
        async with session_scope() as own_session:
            result = await _analyze(contents, own_session,
                                    fingerprints=fingerprints,
                                    bot_account_ids=bot_account_ids,
                                    max_concurrency=max_concurrency,
                                    skip_analyzed=skip_analyzed,
                                    topic_id=topic_id,
                                    client=client)

    logger.info("认知分析完成 | {}", result.stats.render())
    _publish_labeled(result, topic_id=topic_id)
    return result


def _publish_labeled(result: AnalysisResult, *, topic_id: str | None) -> None:
    """通告「这批内容已打标」，驱动预警扫描（T-24 的事件驱动链路）。

    发布点放在这里而不是调用方：忘了发的后果是**预警静默失效**——内容照样入库、
    大盘照样更新，就是一条告警都不出来（设计规格 8.2 要求告警检测是事件驱动而非轮询）。

    事件只带标识与主题，不带标签内容（订阅方自己去分析区读）。发布是同步 O(1) 的：
    订阅方只入队，扫描由它自己的 worker 协程做（见 `alerting/listener.py`）。
    """
    from engines.common.eventing.publishers import publish_content_labeled

    if not result.labels:
        return
    publish_content_labeled(
        [label.content_uid for label in result.labels],
        topic_ids=[topic_id] if topic_id else [],
        negative_count=sum(1 for label in result.labels
                           if label.sentiment == Sentiment.NEGATIVE.value))
