"""态势大盘用例（FR-36~FR-41）。

**读路径自愈**：`get_metrics` 先读物化表，缺失或过期就**同步刷新一次**再返回。
这样处理是因为物化表在两种情况下会是空的——服务刚启动、或后台刷新任务还没跑第一轮；
如果直接返回空指标，大盘首屏就是一屏空图，看起来像坏了。
稳态下（后台任务每 60 秒刷新）读路径只走一次索引查询，符合「首屏 < 3s」的目标。

指标口径集中在这里，三处值得说明：

1. **时间轴是 `analyzed_at`（打标时间）**，与预警的窗口口径一致。持续接入时它与
   发布时间几乎一致；批量补跑的历史数据会让趋势曲线出现一根尖峰（说明见 README）。
2. **热词来自既有结构化产出**（实体名 + 主题词包），不重新分词——理由见 `metrics` 模块。
3. **S/A 级事件数**按事件的**生效风险**统计（人工复核过即人工值），与事件接口同一口径；
   而不是数「有多少条 S/A 级内容」——那是另一个指标，响应里单独给出。
"""

import time

from loguru import logger

from app.services.dashboard import metrics as metrics_module
from app.services.dashboard.repository import (
    SCOPE_ALL,
    DashboardRepository,
    MetricSnapshot,
)
from engines.contracts.enums import EventStatus, RiskLevel

__all__ = [
    "METRIC_KEYS",
    "DEFAULT_TTL_SECONDS",
    "TREND_BUCKET",
    "get_metrics",
    "refresh_scope",
    "refresh_all",
    "to_view",
]

METRIC_KEYS = ("overview", "volume_trend", "sentiment_distribution",
               "source_distribution", "hot_words")

# 物化有效期：后台任务 60 秒一轮，TTL 给 90 秒留出容错（任务慢一轮也不至于让读路径
# 每次都触发同步刷新）。SPEC 8.2 的「每 60s 刷新」对应刷新间隔，不是这个 TTL。
DEFAULT_TTL_SECONDS = 90
REFRESH_INTERVAL_SECONDS = 60

# 趋势粒度：演示数据的打标集中在小时内，按小时看足以看清形态；按天由参数切换。
TREND_BUCKET = "hour"

# 指标计算的时间范围：默认看最近 7 天，避免历史数据无限累积后大盘越来越慢
DEFAULT_WINDOW_HOURS = 24 * 7


async def get_metrics(repo: DashboardRepository, *, event_repo=None, content_repo=None,
                      session=None, topic_id: str | None = None, hours: int | None = None,
                      force_refresh: bool = False) -> dict:
    """取大盘指标（FR-36~FR-40）。

    `hours` 只影响**取数窗口**（趋势与分布的时间范围），不影响物化粒度。
    """
    scope = topic_id or SCOPE_ALL
    snapshots = await repo.read_scope(scope)

    stale = force_refresh or not snapshots or any(item.expired for item in snapshots)
    if stale:
        logger.info("大盘指标缺失或已过期，同步刷新 scope={}", scope)
        snapshots = await refresh_scope(repo, scope=topic_id, event_repo=event_repo,
                                        content_repo=content_repo, session=session,
                                        hours=hours)

    payload = {item.metric_key: item.payload for item in snapshots}
    view = to_view(payload, scope=scope)
    return view


def to_view(payload: dict, *, scope: str) -> dict:
    """把物化内容拼成接口响应。"""
    items = [item for item in (payload.get(key) for key in METRIC_KEYS) if item]
    computed_at = max((item.get("computed_at", 0) for item in items), default=0)
    return {
        "scope": scope,
        "topic_id": None if scope == SCOPE_ALL else scope,
        "computed_at": computed_at,
        "metrics": {
            "overview": payload.get("overview", {}),
            "volume_trend": payload.get("volume_trend", {}),
            "sentiment_distribution": payload.get("sentiment_distribution", {}),
            "source_distribution": payload.get("source_distribution", {}),
            "hot_words": payload.get("hot_words", {}),
        },
    }


async def _high_risk_event_ids(event_repo, content_repo, session,
                               topic_id: str | None = None) -> set[str]:
    """S/A 级事件（按生效风险，与事件接口同口径）。取不到就返回空集合。

    两条口径约定：

    1. **已平息的事件不计入**——大屏上的「S/A 级事件数」是「当前要盯几个」，
       把已平息的历史事件也算进去，这个数字只会随时间单调上升、失去信号意义；
    2. **按主题过滤**：主题维度下不能沿用全局值，否则切到任一主题都显示同一个数。
       `list_events` 的主题筛选是按**内容的归属**实现的（`event.topic_id` 列存的是
       关键词而非主题ID），因此这里直接复用。
    """
    if event_repo is None or content_repo is None or session is None:
        return set()

    from app.services.event import service as event_service

    try:
        _, summaries = await event_service.list_events(
            event_repo, content_repo, session, page=1, page_size=200,
            topic_id=topic_id)
    except Exception as exc:  # noqa: BLE001 —— 事件统计失败不该让大盘整体不可用
        logger.warning("大盘统计 S/A 级事件失败，本次按空集处理：{}", type(exc).__name__)
        return set()

    return {item["event_id"] for item in summaries
            if item.get("risk_level") in (RiskLevel.S.value, RiskLevel.A.value)
            and item.get("status") != EventStatus.RESOLVED.value}


def _trend_start(rows: list, window_start: int) -> int:
    """趋势曲线的起点：取「窗口起点」与「最早一条数据」中的较晚者。

    不这么做的话，窗口（默认 7 天）远大于数据实际跨度时，曲线会是一根尖峰加一百多个
    空桶——既没有信息量（空桶全是 0），又把 payload 撑大几十倍。
    右侧仍以 `now` 为终点，这样持续接入时最右一桶就是「当前小时」。
    """
    published = [row.analyzed_at for row in rows if row.analyzed_at]
    if not published:
        return window_start
    return max(window_start, min(published))


async def refresh_scope(repo: DashboardRepository, *, scope: str | None,
                        event_repo=None, content_repo=None, session=None,
                        hours: int | None = None,
                        ttl_seconds: int = DEFAULT_TTL_SECONDS) -> list[MetricSnapshot]:
    """重算一个范围的全部指标并写回物化表。"""
    now = int(time.time())
    window_start = now - max(1, hours or DEFAULT_WINDOW_HOURS) * 3600

    rows = await repo.analysis_rows(topic_id=scope, start=window_start)
    alerts = await repo.alert_rows()
    open_tickets = await repo.open_ticket_count()
    hot_events = await _high_risk_event_ids(event_repo, content_repo, session,
                                           topic_id=scope)

    payloads: dict[str, dict] = {
        "overview": metrics_module.build_overview(
            rows, alerts=alerts, open_tickets=open_tickets, hot_event_ids=hot_events),
        "volume_trend": metrics_module.build_volume_trend(
            rows, bucket=TREND_BUCKET, start=_trend_start(rows, window_start), end=now),
        "sentiment_distribution": metrics_module.build_sentiment_distribution(rows),
        "source_distribution": metrics_module.build_source_distribution(rows),
        "hot_words": metrics_module.build_hot_words(
            rows, entity_mentions=await repo.entity_mentions(topic_id=scope),
            keyword_mentions=await repo.keyword_mentions(topic_id=scope)),
    }

    stored: list[MetricSnapshot] = []
    for key, payload in payloads.items():
        payload = {**payload, "computed_at": now, "window_hours": hours or DEFAULT_WINDOW_HOURS}
        stored.append(await repo.upsert_metric(
            key, scope=scope or SCOPE_ALL, bucket=TREND_BUCKET if key == "volume_trend"
            else "static", payload=payload, ttl_seconds=ttl_seconds))

    logger.info("大盘指标已刷新 scope={} 内容 {} 条 / 告警 {} 条 / 待处置工单 {}",
                scope or SCOPE_ALL, len(rows), len(alerts), open_tickets)
    return stored


async def refresh_all(repo: DashboardRepository, *, topic_ids: list[str],
                      event_repo=None, content_repo=None, session=None,
                      hours: int | None = None) -> dict[str, int]:
    """刷新全局与各主题的指标（后台任务与 CLI 的入口）。

    主题维度是**必刷**的：大盘支持按主题切换，缺了主题指标时用户切过去就是空图，
    而「切过去才有数据」这种补算在用户眼里和卡顿没区别。
    """
    result: dict[str, int] = {}
    for scope in [None, *topic_ids]:
        snapshots = await refresh_scope(repo, scope=scope, event_repo=event_repo,
                                        content_repo=content_repo, session=session,
                                        hours=hours)
        result[scope or SCOPE_ALL] = len(snapshots)
    return result
