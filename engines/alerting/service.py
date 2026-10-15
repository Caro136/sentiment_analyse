"""预警扫描：规则匹配 → 收敛 → 落库 → 通知（FR-52~FR-55）。

**触发方式有两种，走的是同一条链路**：

1. **事件驱动**（主路径）：analytics 打标完成后发布 `CONTENT_LABELED`，
   `listener` 入队，worker 协程按主题扫一遍窗口（设计规格 8.2：告警检测是事件驱动）；
2. **显式补扫**：`scan()` 直接调用，用于演示、补跑与排障（存量数据打标时
   T-24 还不存在，靠它把历史告警补出来）。

执行顺序固定为「**先落库，再通知**」：邮件可能失败并降级，
但告警记录绝不能因为通知发不出去而丢失——它是后续处置的唯一凭据。

**窗口的时间轴是 `analyzed_at`（打标时间）**，不是发布时间：激增规则的语义是
「新内容涌入的速度」，而发布时间只在原始表里（跨异构表无法用一条 SQL 表达）。
持续接入时两者几乎一致；对批量补跑的历史数据，数字反映的是那次批量打标，
这一点在 README 的数据状态里写明，不在代码里假装没有。

**规则按主题分别评估**：某主题的负向激增不该被其他主题的平静稀释掉。
未归属主题的内容单独成组（`topic_id=None`），照样参与判定。
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import select

from engines.alerting import repository as repo
from engines.alerting.lifecycle import can_transition, transition_error
from engines.alerting.notifiers import email_notifier
from engines.alerting.rules import RuleSpec, build_window_stats, evaluate_rules
from engines.common.db.models import ContentAnalysis
from engines.contracts.content import Author, UnifiedContent
from engines.contracts.enums import AlertStatus
from engines.contracts.events import ContentLabeledEvent
from engines.ingestion.reader import load_contents

__all__ = ["ScanResult", "scan", "scan_event", "acknowledge", "DEFAULT_WINDOW_MINUTES",
           "DEFAULT_DEDUP_MINUTES", "build_alert_message"]

# 扫描窗口（分钟）：PRD 附录 B.4 的「单小时负向声量环比激增」
DEFAULT_WINDOW_MINUTES = 60
# 收敛窗口（分钟）：FR-55「同一事件短时内重复触发合并为一条告警」
DEFAULT_DEDUP_MINUTES = 30


@dataclass
class ScanResult:
    """一次扫描的结果。字段刻意给全，便于在日志与自检里一眼看清「为什么没报警」。"""

    scanned_topics: int = 0
    window_contents: int = 0
    hits: int = 0
    created: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    notified: int = 0
    degraded: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        return (f"扫描 {self.scanned_topics} 个主题 / 窗口内 {self.window_contents} 条内容 → "
                f"命中 {self.hits}，新建告警 {len(self.created)}，收敛合并 {len(self.merged)}；"
                f"通知 送达 {self.notified} / 降级 {self.degraded} / 跳过 {self.skipped}")


async def _load_window(session, start: int, end: int) -> dict[str | None, list[dict]]:
    """按主题分组取窗口内的标签。

    返回 {topic_id: [{"content_uid", "sentiment", "risk_level"}]}。
    `analyzed_at` 是打标时间戳（见模块说明）。
    """
    rows = (await session.execute(
        select(ContentAnalysis.content_uid, ContentAnalysis.topic_id,
               ContentAnalysis.sentiment, ContentAnalysis.risk_level)
        .where(ContentAnalysis.analyzed_at >= start, ContentAnalysis.analyzed_at < end))
    ).all()

    grouped: dict[str | None, list[dict]] = {}
    for content_uid, topic_id, sentiment, risk_level in rows:
        grouped.setdefault(topic_id, []).append({
            "content_uid": content_uid, "sentiment": sentiment, "risk_level": risk_level})
    return grouped


def _to_entries(records: list[dict],
                contents: dict[str, UnifiedContent]) -> list[tuple[UnifiedContent, str, str | None]]:
    """把标签行与原文配对；原文缺失的条目跳过（并在调用方记数）。

    跳过而不是报错：原始表里可能已经清理了历史内容，但这类情况应当是「少一条」，
    不该让整次扫描失败——那会让所有真实告警都报不出来。
    """
    entries: list[tuple[UnifiedContent, str, str | None]] = []
    for record in records:
        content = contents.get(record["content_uid"])
        if content is not None:
            entries.append((content, record["sentiment"], record["risk_level"]))
    return entries


async def _resolve_event_id(session, topic_id: str | None,
                            topic_keyword: str | None) -> str | None:
    """把主题映射到事件专题的 ID（收敛键要它）。

    事件由主题关键词派生（`evt_<keyword>`，见 analytics 的 `ensure_event`），
    缺失时这里补建一次——收敛与「按事件看告警」都需要一个稳定的归属，
    没有事件键就只能按 null 收敛，那会把所有主题的告警混成一条。
    """
    if not topic_keyword:
        return None
    from engines.analytics.repository import ensure_event

    return await ensure_event(session, topic_keyword, title=topic_keyword,
                              topic_id=topic_id)


async def scan(*, session=None, window_minutes: int = DEFAULT_WINDOW_MINUTES,
               dedup_minutes: int = DEFAULT_DEDUP_MINUTES, now: int | None = None,
               notify: bool = True, settings=None) -> ScanResult:
    """扫描一次并把命中的规则落成告警。

    `session` 为 None 时自建事务（供 CLI / 补扫入口使用）；由监听器调用时传入
    既有会话，与事件处理共用一次事务。
    """
    from engines.common.db.session import session_scope

    if session is None:
        async with session_scope() as owned:
            return await scan(session=owned, window_minutes=window_minutes,
                              dedup_minutes=dedup_minutes, now=now, notify=notify,
                              settings=settings)
    result = await _scan(session, window_minutes=window_minutes,
                         dedup_minutes=dedup_minutes, now=now, notify=notify,
                         settings=settings)
    return result


async def scan_event(event: ContentLabeledEvent, session) -> ScanResult:
    """事件驱动入口：收到「内容已打标」后扫一遍窗口。"""
    return await scan(session=session)


async def _scan(session, *, window_minutes: int, dedup_minutes: int,
                now: int | None, notify: bool, settings) -> ScanResult:
    result = ScanResult()
    rules = await repo.list_rules(session, enabled_only=True)
    if not rules:
        logger.info("没有启用中的预警规则，跳过扫描")
        return result

    specs = [RuleSpec(rule_id=rule.rule_id, rule_type=rule.rule_type, level=rule.level,
                      threshold=rule.threshold, sensitive_words=rule.sensitive_words)
             for rule in rules]
    sensitive_words = tuple(dict.fromkeys(
        word for spec in specs for word in spec.sensitive_words))

    end = now or int(time.time())
    window_seconds = max(1, window_minutes) * 60
    current = await _load_window(session, end - window_seconds, end)
    previous = await _load_window(session, end - 2 * window_seconds, end - window_seconds)

    all_uids = [row["content_uid"] for rows in (*current.values(), *previous.values())
                for row in rows]
    contents = await load_contents(all_uids, session) if all_uids else {}

    result.scanned_topics = len(current)
    result.window_contents = sum(len(rows) for rows in current.values())
    if not result.window_contents:
        logger.info("窗口内没有新打标的内容（{} 分钟），无需评估", window_minutes)
        return result

    dedup_since = end - max(1, dedup_minutes) * 60

    for topic_id, records in current.items():
        entries = _to_entries(records, contents)
        if not entries:
            continue
        prev_entries = _to_entries(previous.get(topic_id, []), contents)

        stats = build_window_stats(entries, prev_entries, sensitive_words=sensitive_words,
                                   kol_threshold=Author.KOL_INFLUENCE_THRESHOLD)
        hits = evaluate_rules(specs, stats)
        if not hits:
            continue

        topics = stats.top_topics
        event_id = await _resolve_event_id(session, topic_id, topics[0] if topics else None)

        for hit in hits:
            result.hits += 1
            detail = {**hit.detail, "topic_id": topic_id, "reason": hit.reason}
            await _persist(session, result, rule=hit.rule, event_id=event_id,
                           detail=detail, dedup_since=dedup_since,
                           notify=notify, settings=settings)

    logger.info(result.render())
    return result


async def _persist(session, result: ScanResult, *, rule: RuleSpec, event_id: str | None,
                   detail: dict[str, Any], dedup_since: int, notify: bool,
                   settings) -> None:
    """落库（新建或收敛合并），随后通知。"""
    payload = json.dumps(detail, ensure_ascii=False)
    existing = None
    if event_id:
        existing = await repo.find_open_alert(session, rule_id=rule.rule_id,
                                              event_id=event_id, since=dedup_since)

    if existing is not None:
        merged = await repo.merge_alert(session, existing.alert_id, level=rule.level,
                                        detail=payload)
        if merged is None:
            return
        result.merged.append(merged.alert_id)
        logger.info("告警收敛合并 alert_id={} 累计 {} 次：{}",
                    merged.alert_id, merged.merged_count, detail.get("reason", ""))
        # 收敛的告警**不再重复通知**：重复推送会让收件人忽略整个渠道
        return

    snapshot = await repo.create_alert(session, alert_id=repo.new_alert_id(),
                                       rule_id=rule.rule_id, event_id=event_id,
                                       level=rule.level, detail=payload)
    result.created.append(snapshot.alert_id)
    logger.warning("新告警 level={} alert_id={} 事件={} | {}",
                   rule.level, snapshot.alert_id, event_id, detail.get("reason", ""))

    if not notify:
        return

    subject, body = build_alert_message(snapshot, detail)
    send_result = await email_notifier.notify(subject, body, settings=settings)
    await repo.update_notify_result(session, snapshot.alert_id,
                                    status=send_result.status, error=send_result.error)
    if send_result.delivered:
        result.notified += 1
    elif send_result.status == email_notifier.SEND_STATUS_SKIPPED:
        result.skipped += 1
    else:
        result.degraded += 1


def build_alert_message(snapshot: repo.AlertRecordSnapshot,
                        detail: dict[str, Any]) -> tuple[str, str]:
    """构造告警邮件的标题与正文（纯函数，便于测试与预览）。"""
    topic = detail.get("topic_id") or "未归属主题"
    subject = f"[舆情告警·{snapshot.level}级] {topic}"
    lines = [
        f"告警级别：{snapshot.level}",
        f"归属主题：{topic}",
        f"关联事件：{snapshot.event_id or '（未关联事件）'}",
        f"触发规则：{snapshot.rule_id or '-'}",
        f"触发时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(snapshot.triggered_at))}",
        "",
        f"判定依据：{detail.get('reason', '')}",
        "",
    ]

    if detail.get("rule_type") == "negative_surge":
        lines += [
            f"本窗口负向 {detail.get('negative_count')} 条，"
            f"上一窗口 {detail.get('prev_negative_count')} 条，"
            f"环比 {float(detail.get('surge_ratio') or 0) * 100:.0f}%",
        ]
    elif detail.get("rule_type") == "sensitive_hit":
        lines.append(f"命中敏感词：{', '.join(detail.get('words', [])[:10])}")
        for hit in (detail.get("hits") or [])[:5]:
            lines.append(f"  - [{hit.get('risk_level')}] {hit.get('text', '')}")
    else:
        for hit in (detail.get("hits") or [])[:5]:
            lines.append(f"  - {hit.get('nickname')}（影响力 {hit.get('influence_score')}）"
                         f"[{hit.get('risk_level')}] {hit.get('text', '')}")

    lines += ["", "请登录平台查看详情并认领处置：/workbench", "", "— 智能舆情分析平台"]
    return subject, "\n".join(lines)


# ---------------------------------------------------------------------------
# 生命周期（FR-56）
# ---------------------------------------------------------------------------

async def acknowledge(session, alert_id: str, *, status: str) -> repo.AlertRecordSnapshot:
    """流转告警状态。

    非法迁移直接报错（BIZ_203），不做「尽力而为」的兜底——把「已关闭」的告警
    改回「未读」这种事一旦被允许，告警列表的可信度就没了。

    **审计不在本层**：`@audit` 属于接入层（`app/services/audit`），
    引擎层不得反向依赖 app。
    """
    current = await repo.get_alert(session, alert_id)
    if current is None:
        from app.errors import not_found

        raise not_found(f"告警不存在：{alert_id}", detail=f"alert_id={alert_id}")

    if not can_transition(current.status, status):
        from app.errors import conflict

        raise conflict(transition_error(current.status, status),
                       detail=f"from={current.status} to={status}")

    responded_at = int(time.time()) if AlertStatus(status) is AlertStatus.CLAIMED else None
    updated = await repo.update_alert_status(session, alert_id, status=status,
                                             responded_at=responded_at)
    if updated is None:
        from app.errors import not_found

        raise not_found(f"告警不存在：{alert_id}")
    logger.info("告警状态流转 alert_id={} {} → {}", alert_id, current.status, status)
    return updated
