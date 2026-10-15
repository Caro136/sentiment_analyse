"""大盘指标的聚合计算（FR-36~FR-40）。

**全是纯函数**：输入原始行（标签、内容、告警、工单），输出可直接物化的 JSON。
这样口径可以脱离数据库单独测试——大盘数字错了不会报错，只会让人看到错的态势。

五个指标：

| 指标键 | 对应 FR | 内容 |
|--------|---------|------|
| `overview` | FR-40 | 核心指标卡片：总声量、负面率、S/A 级事件数、待处置工单数 |
| `volume_trend` | FR-36 | 按小时/天的声量曲线（总量 + 负面量） |
| `sentiment_distribution` | FR-37 | 正/中/负占比 |
| `source_distribution` | FR-36 的维度切换 | 按信源的内容分布 |
| `hot_words` | FR-39 | 热词及权重 |

**热词不重新分词**：词频来自**既有的结构化产出**——主题词包的词与实体抽取出的
实体名。理由有两条：① 复用分析结果而不是另起一套口径（ADR-02 的同一条纪律）；
② 通用分词（jieba）在舆情短文本上会切出大量噪声词与停用词，而「星耀科技」
「散热争议」这类**实体与词包词**才是运营真正关心的热词。
"""

from collections import Counter
from dataclasses import dataclass, field

from engines.contracts.enums import RiskLevel, Sentiment, SourceType

__all__ = [
    "MetricRow",
    "AlertRow",
    "TrendPoint",
    "build_overview",
    "build_volume_trend",
    "build_sentiment_distribution",
    "build_source_distribution",
    "build_hot_words",
    "HOUR_SECONDS",
]

HOUR_SECONDS = 3600

# 热词权重：命中一次实体记 1，命中一次词包词记 1；标题/正文里再次出现不再加权。
# 保持简单可解释——权重的可解释性比精细度重要，否则没人能回答「这个词为什么排第一」。
WORD_WEIGHT_ENTITY = 1.0
WORD_WEIGHT_KEYWORD = 1.2      # 词包词是使用者明确关心的对象，略高


@dataclass(frozen=True)
class MetricRow:
    """算指标需要的最小标签字段。"""

    content_uid: str
    source_type: str
    sentiment: str
    risk_level: str | None = None
    topic_id: str | None = None
    analyzed_at: int = 0
    engagement_total: int = 0


@dataclass(frozen=True)
class AlertRow:
    """告警的最小字段（核心指标卡片的 S/A 级事件数要用）。"""

    level: str
    status: str
    event_id: str | None = None


@dataclass
class TrendPoint:
    bucket_start: int
    total: int = 0
    negative: int = 0


def build_overview(rows: list[MetricRow], *, alerts: list[AlertRow] | None = None,
                   open_tickets: int = 0, hot_event_ids: set[str] | None = None,
                   ) -> dict:
    """核心指标卡片（FR-40）。

    `hot_event_ids` 是「S/A 级事件」的来源：由调用方从事件统计里取（事件的生效风险
    是现算的，指标层不重新判定），缺省时退化为按标签里的 S/A 内容计数——
    两者口径不同，因此响应里分别给出，不混为一个数。
    """
    total = len(rows)
    negative = sum(1 for row in rows if row.sentiment == Sentiment.NEGATIVE.value)
    high_risk = sum(1 for row in rows
                    if row.risk_level in (RiskLevel.S.value, RiskLevel.A.value))
    sources = {row.source_type for row in rows}
    published = [row.analyzed_at for row in rows if row.analyzed_at]

    open_alerts = [a for a in (alerts or []) if a.status not in ("closed",)]
    high_alerts = [a for a in open_alerts if a.level in (RiskLevel.S.value,
                                                        RiskLevel.A.value)]

    return {
        "total_volume": total,
        "negative_count": negative,
        "negative_ratio": (round(negative / total, 4) if total else None),
        "high_risk_content_count": high_risk,
        "source_count": len(sources),
        "open_alert_count": len(open_alerts),
        "high_level_alert_count": len(high_alerts),
        "high_risk_event_count": (len(hot_event_ids) if hot_event_ids is not None else None),
        "open_ticket_count": open_tickets,
        "first_analyzed_at": min(published) if published else None,
        "last_analyzed_at": max(published) if published else None,
    }


def build_volume_trend(rows: list[MetricRow], *, bucket: str = "hour",
                       start: int | None = None, end: int | None = None) -> dict:
    """声量趋势（FR-36）。按小时或天分桶，空桶补 0。

    **空桶必须补 0**：折线图直接连空桶会导致「没有数据的那几个小时」被视觉上抹平，
    看起来像舆情一直在高位——这在大盘上是最容易误导人的一处。
    """
    seconds = HOUR_SECONDS if bucket == "hour" else HOUR_SECONDS * 24
    buckets: dict[int, TrendPoint] = {}

    for row in rows:
        if not row.analyzed_at:
            continue
        key = (row.analyzed_at // seconds) * seconds
        point = buckets.setdefault(key, TrendPoint(bucket_start=key))
        point.total += 1
        if row.sentiment == Sentiment.NEGATIVE.value:
            point.negative += 1

    if start is not None and end is not None:
        cursor = (start // seconds) * seconds
        while cursor <= end:
            buckets.setdefault(cursor, TrendPoint(bucket_start=cursor))
            cursor += seconds

    points = [buckets[key] for key in sorted(buckets)]
    return {
        "bucket": bucket,
        "points": [
            {"bucket_start": point.bucket_start, "total": point.total,
             "negative": point.negative}
            for point in points
        ],
        "bucket_seconds": seconds,
    }


def build_sentiment_distribution(rows: list[MetricRow]) -> dict:
    """情感分布（FR-37）。"""
    counts = {sentiment.value: 0 for sentiment in Sentiment}
    for row in rows:
        counts[row.sentiment] = counts.get(row.sentiment, 0) + 1
    total = sum(counts.values())
    return {
        "total": total,
        "counts": counts,
        "ratios": {key: (round(value / total, 4) if total else 0.0)
                   for key, value in counts.items()},
    }


def build_source_distribution(rows: list[MetricRow]) -> dict:
    """按信源的分布（FR-36 的维度切换要用）。"""
    counts: Counter[str] = Counter(row.source_type for row in rows)
    total = sum(counts.values())
    items = []
    for source_type in SourceType:                     # 固定顺序，避免顺序随数据漂移
        count = counts.get(source_type.value, 0)
        items.append({
            "source_type": source_type.value,
            "label": source_type.label,
            "count": count,
            "ratio": (round(count / total, 4) if total else 0.0),
        })
    return {"total": total, "items": items}


def build_hot_words(rows: list[MetricRow], *, entity_mentions: Counter[str] | None = None,
                    keyword_mentions: Counter[str] | None = None, limit: int = 30) -> dict:
    """热词云（FR-39）。

    `entity_mentions` / `keyword_mentions` 由调用方从实体表与主题词包统计传入
    （本函数不查库，保持纯函数）。返回按权重倒序的词列表，附带来源标记——
    前端可据此区分「实体」与「词包词」，运营看到热词时知道它是怎么来的。
    """
    scores: dict[str, float] = {}
    kinds: dict[str, str] = {}

    for word, count in (entity_mentions or Counter()).items():
        if not word:
            continue
        scores[word] = scores.get(word, 0.0) + count * WORD_WEIGHT_ENTITY
        kinds.setdefault(word, "entity")

    for word, count in (keyword_mentions or Counter()).items():
        if not word:
            continue
        scores[word] = scores.get(word, 0.0) + count * WORD_WEIGHT_KEYWORD
        kinds[word] = "keyword"                        # 词包词优先标注（使用者明确关心）

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return {
        "total_terms": len(scores),
        "items": [{"word": word, "weight": round(weight, 2), "kind": kinds.get(word, "entity")}
                  for word, weight in ranked],
    }


def total_engagement(rows: list[tuple[MetricRow, int]]) -> int:
    """互动量总和（供核心指标卡片的扩展位使用）。"""
    return sum(value for _, value in rows)
