"""预警规则评估（PRD 附录 B.4 / FR-52）。

三类规则**全是纯函数**：输入窗口内的内容与它们的标签，输出命中列表。
这样规则逻辑可以脱离数据库与网络单独测试——预警的判定标准出错时不会有任何异常，
只会「该报的没报」或「不该报的报了」，那是最难发现的一类问题。

三类规则（满足任一即触发）：

| 类型 | 判定 | 关键参数 |
|------|------|---------|
| `negative_surge` | 本窗口负向条数相对上一窗口的增幅超过阈值 | `threshold`（0.5 表示 +50%） |
| `sensitive_hit` | 窗口内命中敏感词库的内容达到阈值条数 | `sensitive_words` + `threshold`（条数） |
| `kol_voice` | 窗口内高权重账号（达 KOL 阈值）发出负向或 A/S 级内容 | `threshold`（条数） |

**时间轴用 `analyzed_at`（打标时间）而非发布时间**：激增规则的语义是「新内容涌入的速度」，
而发布时间属于原始表（分析区没有该列，跨表 JOIN 无法用一条 SQL 表达）。
两者在持续接入的链路里几乎一致（内容入库即打标）；批量补跑历史数据时会有偏差，
这一点写在 README 的数据状态里，不在代码里假装没有。
"""

from dataclasses import dataclass, field
from typing import Any

from engines.contracts.content import Author, UnifiedContent
from engines.contracts.enums import AlertRuleType, RiskLevel, Sentiment

__all__ = [
    "RuleSpec",
    "WindowStats",
    "RuleEvaluation",
    "SURGE_ZERO_BASE_FLOOR",
    "SURGE_MIN_NEGATIVE",
    "build_window_stats",
    "evaluate_rules",
]

# 上一窗口为 0 时的基数下限：直接用 0 做分母会得到「增幅无穷大」，
# 一条负向就能触发 S 级告警。取 1 表示「从 0 到 N 条算作 N 倍」，
# 同时配合下面的最小条数门槛，避免个位数波动误报。
SURGE_ZERO_BASE_FLOOR = 1

# 激增规则的最小绝对条数：没有这个门槛，1 条 → 3 条也会被判为「+200%」
SURGE_MIN_NEGATIVE = 5


@dataclass(frozen=True)
class RuleSpec:
    """一条启用中的规则（`alert_rule` 行的快照）。"""

    rule_id: str
    rule_type: str
    level: str
    threshold: float | None = None
    sensitive_words: tuple[str, ...] = ()


@dataclass
class WindowStats:
    """窗口统计。`prev_*` 是上一个等长窗口的对应数值，用于算环比。"""

    negative_count: int = 0
    total_count: int = 0
    prev_negative_count: int = 0
    prev_total_count: int = 0
    top_topics: list[str] = field(default_factory=list)

    # 命中详情用的样本（最多若干条，避免把几百条 uid 塞进告警明细）
    negative_uids: list[str] = field(default_factory=list)
    sensitive_hits: list[dict[str, Any]] = field(default_factory=list)
    kol_hits: list[dict[str, Any]] = field(default_factory=list)

    @property
    def surge_ratio(self) -> float | None:
        """负向声量环比增幅。上一窗口没有数据时返回 None（而不是无穷大）。"""
        if self.prev_negative_count <= 0:
            return None
        return (self.negative_count - self.prev_negative_count) / self.prev_negative_count


@dataclass(frozen=True)
class RuleEvaluation:
    """一次规则命中。"""

    rule: RuleSpec
    reason: str
    detail: dict[str, Any]


def _sample_limit(limit: int = 10) -> int:
    return limit


def build_window_stats(entries: list[tuple[UnifiedContent, str, str | None]],
                       prev_entries: list[tuple[UnifiedContent, str, str | None]],
                       *, sensitive_words: tuple[str, ...] = (),
                       kol_threshold: float = Author.KOL_INFLUENCE_THRESHOLD,
                       ) -> WindowStats:
    """把「窗口内的内容 + 标签」压成统计量。

    `entries` 的每项是 (内容, 情感, 风险等级)；`prev_entries` 是上一个等长窗口的同构数据。

    敏感词与高权重账号的判定放在这里，是因为它们都需要**逐条看内容与作者**，
    与规则阈值无关（阈值只在 `evaluate_rules` 里比大小）。
    """
    stats = WindowStats(
        negative_count=sum(1 for _, sentiment, _ in entries
                           if sentiment == Sentiment.NEGATIVE.value),
        total_count=len(entries),
        prev_negative_count=sum(1 for _, sentiment, _ in prev_entries
                                if sentiment == Sentiment.NEGATIVE.value),
        prev_total_count=len(prev_entries),
    )

    topics: dict[str, int] = {}
    for content, sentiment, risk in entries:
        if content.source_keyword:
            topics[content.source_keyword] = topics.get(content.source_keyword, 0) + 1

        if sentiment == Sentiment.NEGATIVE.value and len(stats.negative_uids) < _sample_limit():
            stats.negative_uids.append(content.content_uid)

        if sensitive_words:
            matched = [word for word in sensitive_words
                       if word and word in (content.raw_text or content.text)]
            if matched:
                stats.sensitive_hits.append({
                    "content_uid": content.content_uid,
                    "words": matched[:5],
                    "sentiment": sentiment,
                    "risk_level": risk,
                    "text": (content.raw_text or content.text)[:80],
                })

        high_risk = risk in (RiskLevel.S.value, RiskLevel.A.value)
        if content.author.influence_score >= kol_threshold and (
                sentiment == Sentiment.NEGATIVE.value or high_risk):
            stats.kol_hits.append({
                "content_uid": content.content_uid,
                "nickname": content.author.nickname,
                "influence_score": content.author.influence_score,
                "follower_count": content.author.follower_count,
                "sentiment": sentiment,
                "risk_level": risk,
                "text": (content.raw_text or content.text)[:80],
            })

    stats.sensitive_hits = stats.sensitive_hits[:_sample_limit()]
    stats.kol_hits = sorted(stats.kol_hits,
                            key=lambda item: item["influence_score"],
                            reverse=True)[:_sample_limit()]
    stats.top_topics = sorted(topics, key=lambda k: topics[k], reverse=True)[:5]
    return stats


def evaluate_rules(rules: list[RuleSpec], stats: WindowStats) -> list[RuleEvaluation]:
    """逐条规则判定是否命中。返回命中列表（可能为空）。"""
    hits: list[RuleEvaluation] = []

    for rule in rules:
        try:
            rule_type = AlertRuleType(rule.rule_type)
        except ValueError:
            # 未知规则类型不报错也不放过：忽略它但留出线索（调用方会记日志）
            continue

        if rule_type is AlertRuleType.NEGATIVE_SURGE:
            hit = _evaluate_surge(rule, stats)
        elif rule_type is AlertRuleType.SENSITIVE_HIT:
            hit = _evaluate_sensitive(rule, stats)
        else:
            hit = _evaluate_kol(rule, stats)

        if hit is not None:
            hits.append(hit)

    return hits


def _evaluate_surge(rule: RuleSpec, stats: WindowStats) -> RuleEvaluation | None:
    threshold = 0.5 if rule.threshold is None else rule.threshold
    if stats.negative_count < SURGE_MIN_NEGATIVE:
        return None

    ratio = stats.surge_ratio
    if stats.prev_negative_count <= 0:
        # 上一窗口没有负向：以 1 为基数算「从 0 起跳」的增幅，并如实标注基数
        baseline = SURGE_ZERO_BASE_FLOOR
        ratio = (stats.negative_count - baseline) / baseline
        base_note = "上一窗口无负向，按基数 1 计"
    else:
        base_note = ""

    if ratio is None or ratio < threshold:
        return None

    return RuleEvaluation(
        rule=rule,
        reason=(f"负向声量环比激增 {ratio * 100:.0f}%（阈值 {threshold * 100:.0f}%）："
                f"本窗口 {stats.negative_count} 条 vs 上一窗口 {stats.prev_negative_count} 条"),
        detail={
            "rule_type": rule.rule_type,
            "negative_count": stats.negative_count,
            "prev_negative_count": stats.prev_negative_count,
            "total_count": stats.total_count,
            "prev_total_count": stats.prev_total_count,
            "surge_ratio": round(ratio, 4),
            "threshold": threshold,
            "note": base_note,
            "sample_uids": stats.negative_uids,
            "top_topics": stats.top_topics,
        },
    )


def _evaluate_sensitive(rule: RuleSpec, stats: WindowStats) -> RuleEvaluation | None:
    threshold = 1.0 if rule.threshold is None else rule.threshold
    if len(stats.sensitive_hits) < threshold:
        return None

    words = sorted({word for hit in stats.sensitive_hits for word in hit["words"]})
    return RuleEvaluation(
        rule=rule,
        reason=(f"命中高危敏感词 {len(stats.sensitive_hits)} 条"
                f"（阈值 {threshold:g} 条）：{', '.join(words[:6])}"),
        detail={
            "rule_type": rule.rule_type,
            "hit_count": len(stats.sensitive_hits),
            "threshold": threshold,
            "words": words[:20],
            "hits": stats.sensitive_hits,
        },
    )


def _evaluate_kol(rule: RuleSpec, stats: WindowStats) -> RuleEvaluation | None:
    threshold = 1.0 if rule.threshold is None else rule.threshold
    if len(stats.kol_hits) < threshold:
        return None

    top = stats.kol_hits[0]
    return RuleEvaluation(
        rule=rule,
        reason=(f"高权重账号发声 {len(stats.kol_hits)} 条（阈值 {threshold:g} 条）："
                f"{top['nickname']}（影响力 {top['influence_score']:.1f}）"),
        detail={
            "rule_type": rule.rule_type,
            "hit_count": len(stats.kol_hits),
            "threshold": threshold,
            "hits": stats.kol_hits,
        },
    )
