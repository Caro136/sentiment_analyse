"""测试替身。

**内存用户仓储**：让鉴权相关的测试完全离线——不连 MySQL、不建表、不依赖
网络。这是本项目的测试纪律（见 tests/__init__.py）：每个任务新增的能力都要
能在 `python -m unittest discover` 里几秒内跑完。

替身与 `SqlUserRepository` 的契约一致性由 `test_auth_api.py::RepositoryParityTest`
用同一组用例覆盖两侧的公共行为来保证——替身跑偏了会立刻暴露，
而不是等到接真数据库时才发现「测试全绿但线上是坏的」。

**审计捕获**：审计写库走的是独立事务（见 `app/services/audit/writer.py`），
测试里把它换成一个只收集 `AuditLog` 对象的假会话。这样跑测试不必有数据库，
但走的仍是真实的 `write_entry` 代码（序列化、失败描述、操作人/IP 回退都在里面），
而不是把 `write_entry` 整个替换掉。
"""

import time
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import replace
from unittest.mock import patch

from app.services.auth import passwords
from app.services.auth.repository import UserRecord, new_user_id
from app.services.content.repository import AnalysisRecord
from app.services.monitoring.repository import (
    ChannelRecord,
    KeywordRecord,
    TopicRecord,
)
from engines.contracts.content import Author, Engagement, UnifiedContent, make_content_uid
from engines.contracts.enums import SourceType
from engines.ingestion.reader import RawHit

__all__ = [
    "InMemoryUserRepository",
    "InMemoryMonitoringRepository",
    "InMemoryContentRepository",
    "InMemoryEventRepository",
    "make_user",
    "make_topic",
    "make_analysis",
    "make_content",
    "make_raw_hit",
    "make_event",
    "make_cluster",
    "make_alert",
    "make_ticket",
    "InMemoryAlertStore",
    "InMemoryTicketRepository",
    "capture_audit_writes",
    "capture_probe",
    "patch_raw_layer",
]


class _FakeSession:
    """只实现 `add` 的假会话——审计写入用到的全部接口。"""

    def __init__(self, sink: list) -> None:
        self._sink = sink

    def add(self, obj) -> None:
        self._sink.append(obj)


def silence_app_logging():
    """关掉 `create_app()` 里的日志初始化。

    每个接口用例都会新建一次应用，而 `setup_logging` 会重建 loguru 的 7 个 sink
    （每个 `enqueue=True` 的 sink 都要起一个线程、开一个文件）——实测这是
    接口测试里最大的一笔固定开销。测试不关心日志落到哪个文件，直接关掉。
    """
    return patch("app.main.setup_logging")


def capture_audit_writes(rows: list):
    """返回一个把审计写入收集到 `rows` 的 patcher（`start()` / `stop()`）。"""
    @asynccontextmanager
    async def _scope():
        yield _FakeSession(rows)

    return patch("app.services.audit.writer.session_scope", _scope)


def make_topic(topic_id: str = "tp1", *, name: str = "演示主题", owner_id: str = "u-1",
               status: str = "enabled", source_keyword: str | None = None,
               description: str = "") -> TopicRecord:
    return TopicRecord(
        topic_id=topic_id,
        name=name,
        description=description,
        source_keyword=source_keyword,
        owner_id=owner_id,
        status=status,
        start_at=None,
        created_at=int(time.time()),
    )


class InMemoryMonitoringRepository:
    """`MonitoringRepository` 的内存实现（主题 / 词包 / 通道 / 关联）。"""

    def __init__(self, topics: list[TopicRecord] | None = None,
                 channels: list[ChannelRecord] | None = None) -> None:
        self.topics: dict[str, TopicRecord] = {t.topic_id: t for t in (topics or [])}
        self.channels: dict[str, ChannelRecord] = {c.channel_id: c for c in (channels or [])}
        self.keywords: dict[str, list[KeywordRecord]] = {}
        self.links: dict[str, list[str]] = {}

    # -- 主题 --
    async def list_topics(self, *, page: int, page_size: int, status: str | None = None,
                          owner_id: str | None = None, keyword: str | None = None,
                          ) -> tuple[int, list[TopicRecord]]:
        rows = list(self.topics.values())
        if status:
            rows = [t for t in rows if t.status == status]
        if owner_id:
            rows = [t for t in rows if t.owner_id == owner_id]
        if keyword:
            rows = [t for t in rows if keyword in t.name]
        rows.sort(key=lambda t: t.created_at, reverse=True)
        start = (page - 1) * page_size
        return len(rows), rows[start:start + page_size]

    async def get_topic(self, topic_id: str) -> TopicRecord | None:
        return self.topics.get(topic_id)

    async def create_topic(self, record: TopicRecord) -> TopicRecord:
        if record.topic_id in self.topics:
            raise ValueError(f"主题已存在：{record.topic_id}")
        self.topics[record.topic_id] = record
        return record

    async def update_topic(self, topic_id: str, fields: dict) -> TopicRecord | None:
        current = self.topics.get(topic_id)
        if current is None:
            return None
        updated = replace(current, **fields)
        self.topics[topic_id] = updated
        return updated

    # -- 词包 --
    async def list_keywords(self, topic_id: str) -> list[KeywordRecord]:
        return list(self.keywords.get(topic_id, []))

    async def list_keywords_for(self, topic_ids: list[str],
                                ) -> dict[str, list[KeywordRecord]]:
        return {tid: list(self.keywords.get(tid, [])) for tid in topic_ids}

    async def replace_keywords(self, topic_id: str,
                               keywords: list[KeywordRecord]) -> None:
        self.keywords[topic_id] = list(keywords)

    # -- 通道 --
    async def list_channels(self) -> list[ChannelRecord]:
        return list(self.channels.values())

    async def get_channel(self, channel_id: str) -> ChannelRecord | None:
        return self.channels.get(channel_id)

    async def create_channel(self, record: ChannelRecord) -> ChannelRecord:
        self.channels[record.channel_id] = record
        return record

    async def update_channel(self, channel_id: str, fields: dict) -> ChannelRecord | None:
        current = self.channels.get(channel_id)
        if current is None:
            return None
        updated = replace(current, **fields)
        self.channels[channel_id] = updated
        return updated

    # -- 关联 --
    async def list_topic_channel_ids(self, topic_id: str) -> list[str]:
        return list(self.links.get(topic_id, []))

    async def replace_topic_channels(self, topic_id: str,
                                     channel_ids: list[str]) -> None:
        self.links[topic_id] = list(dict.fromkeys(channel_ids))


def capture_probe(probe=None, error: Exception | None = None):
    """把通道自检的原始表探测换成替身（离线测试用）。

    探测本身是 ingestion 的能力（真查原始表），此处只验证 monitoring 的组装与
    失败降级，因此替换掉探测函数而不是伪造数据库。
    """
    async def _probe(source_type, session):
        if error is not None:
            raise error
        return probe

    return patch("app.services.monitoring.channels.probe_source", _probe)


# ---------------------------------------------------------------------------
# 内容查询（T-22）
# ---------------------------------------------------------------------------

def make_content(content_uid: str = "social_media:weibo_note:1", *, text: str = "正文字样",
                 title: str = "", published_at: int = 1792000000, url: str = "",
                 source_keyword: str = "", nickname: str = "某用户",
                 likes: int = 0, comments: int = 0, record_type: str = "post",
                 parent_content_uid: str = "", verified_type: str = "普通用户",
                 influence_score: float = 30.0, follower_count: int = 1200,
                 ) -> UnifiedContent:
    source_type, source_table, business_id = content_uid.split(":")
    return UnifiedContent(
        content_uid=content_uid,
        source_type=SourceType(source_type),
        source_table=source_table,
        business_id=int(business_id),
        record_type=record_type,
        parent_content_uid=parent_content_uid,
        author=Author(account_id="acc-1", nickname=nickname, follower_count=follower_count,
                      verified_type=verified_type, influence_score=influence_score),
        title=title,
        text=text,
        raw_text=text,
        published_at=published_at,
        url=url,
        source_keyword=source_keyword,
        engagement=Engagement(likes=likes, comments=comments),
    )


def make_raw_hit(content_uid: str = "social_media:weibo_note:1", *,
                 published_at: int = 1792000000) -> RawHit:
    source_type, source_table, business_id = content_uid.split(":")
    return RawHit(content_uid=content_uid, source_type=SourceType(source_type),
                  source_table=source_table, business_id=int(business_id),
                  published_at=published_at)


def make_analysis(content_uid: str = "social_media:weibo_note:1", *,
                  sentiment: str = "negative", risk_level: str = "A",
                  topic_id: str | None = None, emotion: str | None = "失望",
                  intensity_index: float = 0.42, is_suspected_bot: bool = False,
                  is_manual_override: bool = False, risk_factors: str | None = None,
                  analyzed_at: int = 1792040000) -> AnalysisRecord:
    source_type, source_table, business_id = content_uid.split(":")
    return AnalysisRecord(
        content_uid=content_uid,
        source_type=source_type,
        source_table=source_table,
        business_id=int(business_id),
        topic_id=topic_id,
        sentiment=sentiment,
        emotion=emotion,
        sentiment_confidence=0.88,
        intensity_index=intensity_index,
        risk_level=risk_level,
        risk_factors=risk_factors,
        is_manual_override=is_manual_override,
        override_reason=None,
        simhash="1234567890",
        is_suspected_bot=is_suspected_bot,
        model_version="test-model",
        analyzed_at=analyzed_at,
    )


class InMemoryContentRepository:
    """`ContentRepository` 的内存实现（标签 + 主题名）。"""

    def __init__(self, records: list[AnalysisRecord] | None = None,
                 topics: dict[str, str] | None = None) -> None:
        self.records: dict[str, AnalysisRecord] = {r.content_uid: r for r in (records or [])}
        self.topics: dict[str, str] = dict(topics or {})

    async def query_analysis(self, content_uids: list[str], *, topic_id: str | None = None,
                             sentiment: str | None = None, risk_level: str | None = None,
                             is_suspected_bot: bool | None = None,
                             is_manual_override: bool | None = None,
                             ) -> list[AnalysisRecord]:
        rows = [self.records[uid] for uid in content_uids if uid in self.records]
        if topic_id:
            rows = [r for r in rows if r.topic_id == topic_id]
        if sentiment:
            rows = [r for r in rows if r.sentiment == sentiment]
        if risk_level:
            rows = [r for r in rows if r.risk_level == risk_level]
        if is_suspected_bot is not None:
            rows = [r for r in rows if r.is_suspected_bot == is_suspected_bot]
        if is_manual_override is not None:
            rows = [r for r in rows if r.is_manual_override == is_manual_override]
        return rows

    async def get_analysis(self, content_uid: str) -> AnalysisRecord | None:
        return self.records.get(content_uid)

    async def topic_names(self, topic_ids: list[str]) -> dict[str, str]:
        return {tid: self.topics[tid] for tid in topic_ids if tid in self.topics}


def make_alert(alert_id: str = "alert_1", *, rule_id: str | None = "rule_1",
               event_id: str | None = "evt_1", level: str = "A", status: str = "unread",
               triggered_at: int | None = None, responded_at: int | None = None,
               merged_count: int = 1, detail: dict | None = None,
               notify_status: str | None = "sent") -> "AlertRecordSnapshot":
    from engines.alerting.repository import AlertRecordSnapshot

    return AlertRecordSnapshot(
        alert_id=alert_id, rule_id=rule_id, event_id=event_id, level=level, status=status,
        triggered_at=triggered_at if triggered_at is not None else int(time.time()) - 60,
        responded_at=responded_at, merged_count=merged_count,
        detail=detail or {"rule_type": "negative_surge", "topic_id": "tp1",
                          "reason": "负向声量环比激增 120%"},
        notify_status=notify_status, notify_error=None)


class InMemoryAlertStore:
    """预警规则的存储替身 + 一组 patcher（覆盖 app 层用到的全部引擎函数）。

    `engines/alerting` 是引擎层、SQL 在它自己的 repository 里，因此测试通过
    patch 模块函数来替换——不重写一份 SQL 实现（那样会测试与实现漂移）。
    """

    def __init__(self, rules: list | None = None, alerts: list | None = None,
                 scan_result=None) -> None:
        from engines.alerting.repository import RuleRecord

        self.rules: list[RuleRecord] = list(rules or [])
        self.alerts: list = list(alerts or [])
        self.scan_result = scan_result
        self.scan_calls: list[dict] = []

    def rules_for(self, **kwargs):
        return list(self.rules)

    def rule_for(self, session, rule_id):
        for rule in self.rules:
            if rule.rule_id == rule_id:
                return rule
        return None

    @contextmanager
    def patch(self):
        """把引擎层的读写换成内存实现（含 acknowledge 的状态机）。"""
        from engines.alerting import repository as engine_repo
        from engines.alerting import service as engine_service
        from engines.alerting import lifecycle
        from engines.alerting.repository import AlertRecordSnapshot, RuleRecord

        store = self

        async def _list_rules(session, *, enabled_only=False):
            return [r for r in store.rules if r.enabled or not enabled_only]

        async def _get_rule(session, rule_id):
            return store.rule_for(session, rule_id)

        async def _create_rule(session, *, rule_id, rule_type, level, threshold,
                               sensitive_words, enabled=True):
            record = RuleRecord(rule_id=rule_id, rule_type=rule_type, level=level,
                                threshold=threshold,
                                sensitive_words=tuple(w for w in sensitive_words.split(",") if w),
                                enabled=enabled, updated_at=int(time.time()))
            store.rules.append(record)
            return record

        async def _update_rule(session, rule_id, fields):
            current = store.rule_for(session, rule_id)
            if current is None:
                return None
            updated = replace(current, **fields, updated_at=int(time.time()))
            store.rules[store.rules.index(current)] = updated
            return updated

        async def _list_alerts(session, *, page, page_size, **filters):
            rows = store.alerts
            if filters.get("status"):
                rows = [a for a in rows if a.status == filters["status"]]
            if filters.get("level"):
                rows = [a for a in rows if a.level == filters["level"]]
            if filters.get("event_id"):
                rows = [a for a in rows if a.event_id == filters["event_id"]]
            start = (page - 1) * page_size
            return len(rows), rows[start:start + page_size]

        async def _get_alert(session, alert_id):
            for alert in store.alerts:
                if alert.alert_id == alert_id:
                    return alert
            return None

        async def _update_alert_status(session, alert_id, *, status, responded_at=None):
            current = await _get_alert(session, alert_id)
            if current is None:
                return None
            updated = replace(current, status=status,
                              responded_at=(current.responded_at
                                            if current.responded_at is not None
                                            else responded_at))
            store.alerts[store.alerts.index(current)] = updated
            return updated

        async def _acknowledge(session, alert_id, *, status):
            from app.errors import conflict, not_found
            current = await _get_alert(session, alert_id)
            if current is None:
                raise not_found("告警不存在")
            if not lifecycle.can_transition(current.status, status):
                raise conflict(lifecycle.transition_error(current.status, status))
            return await _update_alert_status(session, alert_id, status=status,
                                              responded_at=int(time.time()))

        async def _scan(session=None, **kwargs):
            """替身只记录调用参数并返回预设结果——规则判定本身由引擎层的测试覆盖。"""
            store.scan_calls.append(kwargs)
            return store.scan_result

        with ExitStack() as stack:
            stack.enter_context(patch.object(engine_repo, "list_rules", _list_rules))
            stack.enter_context(patch.object(engine_repo, "get_rule", _get_rule))
            stack.enter_context(patch.object(engine_repo, "create_rule", _create_rule))
            stack.enter_context(patch.object(engine_repo, "update_rule", _update_rule))
            stack.enter_context(patch.object(engine_repo, "list_alerts", _list_alerts))
            stack.enter_context(patch.object(engine_repo, "get_alert", _get_alert))
            stack.enter_context(patch.object(engine_repo, "update_alert_status",
                                             _update_alert_status))
            stack.enter_context(patch.object(engine_service, "acknowledge", _acknowledge))
            stack.enter_context(patch.object(engine_service, "scan", _scan))
            # app 层用 `from ... import` 直接引用了这两个名字，需要额外打一次
            stack.enter_context(patch("app.services.alert.service.engine.acknowledge",
                                      _acknowledge))
            yield


def make_ticket(ticket_id: str = "tk1", *, event_id: str = "evt_1",
                status: str = "pending", assignee_id: str | None = None,
                risk_level: str | None = "A", response_draft: str | None = None,
                created_at: int | None = None, closed_at: int | None = None,
                alert_id: str | None = None) -> "TicketRecord":
    from app.services.ticket.repository import TicketRecord

    return TicketRecord(
        ticket_id=ticket_id, event_id=event_id, alert_id=alert_id, status=status,
        assignee_id=assignee_id, risk_level=risk_level, response_draft=response_draft,
        created_at=created_at if created_at is not None else int(time.time()) - 300,
        closed_at=closed_at)


class InMemoryTicketRepository:
    """`TicketRepository` 的内存实现（工单 + 流转记录）。"""

    def __init__(self, tickets: list | None = None) -> None:
        self.tickets: dict[str, object] = {t.ticket_id: t for t in (tickets or [])}
        self.logs: dict[str, list] = {}

    async def create_ticket(self, record):
        self.tickets[record.ticket_id] = record
        return record

    async def get_ticket(self, ticket_id: str):
        return self.tickets.get(ticket_id)

    async def find_open_by_event(self, event_id: str):
        for ticket in self.tickets.values():
            if ticket.event_id == event_id and ticket.status not in ("resolved", "ignored"):
                return ticket
        return None

    async def list_tickets(self, *, page, page_size, status=None, event_id=None,
                           assignee_id=None):
        rows = list(self.tickets.values())
        if status:
            rows = [t for t in rows if t.status == status]
        if event_id:
            rows = [t for t in rows if t.event_id == event_id]
        if assignee_id:
            rows = [t for t in rows if t.assignee_id == assignee_id]
        rows.sort(key=lambda t: t.created_at, reverse=True)
        start = (page - 1) * page_size
        return len(rows), rows[start:start + page_size]

    async def update_ticket(self, ticket_id: str, fields: dict):
        current = self.tickets.get(ticket_id)
        if current is None:
            return None
        updated = replace(current, **fields)
        self.tickets[ticket_id] = updated
        return updated

    async def add_log(self, ticket_id: str, record) -> None:
        self.logs.setdefault(ticket_id, []).append(record)

    async def list_logs(self, ticket_id: str) -> list:
        return list(self.logs.get(ticket_id, []))


def make_event(event_id: str = "evt_1", *, title: str = "演示事件",
               topic_id: str | None = None, status: str = "monitoring",
               risk_level: str | None = None, manual_override: bool = False,
               override_reason: str | None = None) -> "EventRecord":
    from app.services.event.repository import EventRecord

    return EventRecord(event_id=event_id, title=title, topic_id=topic_id,
                       status=status, created_at=int(time.time()),
                       risk_level=risk_level, manual_override=manual_override,
                       override_reason=override_reason)


def make_cluster(cluster_id: str = "c1", *, event_id: str = "evt_1",
                 label: str = "簇标签", size: int = 10, sentiment: str | None = "negative",
                 summary: str = "", intent: str | None = None) -> "ClusterRecord":
    from app.services.event.repository import ClusterRecord

    return ClusterRecord(cluster_id=cluster_id, label=label, summary=summary, size=size,
                         sentiment=sentiment, intent=intent, event_id=event_id)


class InMemoryEventRepository:
    """`EventRepository` 的内存实现（事件、关联、聚类、图谱）。"""

    def __init__(self, events: list | None = None,
                 links: dict[str, list[str]] | None = None,
                 clusters: list | None = None,
                 members: dict[str, list[str]] | None = None,
                 representatives: dict[str, list[str]] | None = None,
                 graph: dict | None = None,
                 entity_counts: tuple[int, int] = (0, 0)) -> None:
        self.events = {e.event_id: e for e in (events or [])}
        self.links = dict(links or {})
        self.clusters = list(clusters or [])
        self.members = dict(members or {})
        self.representatives = dict(representatives or {})
        self.graph = graph or {"nodes": [], "edges": []}
        self._entity_counts = entity_counts

    async def list_events(self, *, topic_id=None, status=None, keyword=None) -> list:
        rows = list(self.events.values())
        if status:
            rows = [e for e in rows if e.status == status]
        if keyword:
            rows = [e for e in rows if keyword in e.title]
        rows.sort(key=lambda e: e.created_at)
        return rows

    async def get_event(self, event_id: str):
        return self.events.get(event_id)

    async def content_uids(self, event_ids: list[str]) -> dict[str, list[str]]:
        return {event_id: list(self.links.get(event_id, [])) for event_id in event_ids}

    async def cluster_counts(self, event_ids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cluster in self.clusters:
            if cluster.event_id in event_ids:
                counts[cluster.event_id] = counts.get(cluster.event_id, 0) + 1
        return counts

    async def list_clusters(self, event_id: str) -> list:
        rows = [c for c in self.clusters if c.event_id == event_id]
        rows.sort(key=lambda c: c.size, reverse=True)
        return rows

    async def get_cluster(self, cluster_id: str):
        for cluster in self.clusters:
            if cluster.cluster_id == cluster_id:
                return cluster
        return None

    async def cluster_members(self, cluster_id: str, *, page: int, page_size: int,
                              representatives_only: bool = False):
        source = (self.representatives if representatives_only else self.members)
        uids = list(source.get(cluster_id, []))
        start = (page - 1) * page_size
        return len(uids), uids[start:start + page_size]

    async def entity_graph(self, event_id: str, *, limit: int) -> dict:
        return self.graph

    async def entity_counts(self, event_id: str) -> tuple[int, int]:
        return self._entity_counts

    async def apply_risk_override(self, event_id: str, *, risk_level: str, reason: str):
        current = self.events.get(event_id)
        if current is None:
            return None
        updated = replace(current, risk_level=risk_level, manual_override=True,
                          override_reason=reason)
        self.events[event_id] = updated
        return updated


@contextmanager
def patch_raw_layer(*, hits: list[RawHit], contents: dict[str, UnifiedContent],
                    assets: dict[str, list] | None = None):
    """替换内容服务用到的接入层读取（候选筛选 / 原文回填 / 多模态产物）。

    这三件事都是 ingestion 的能力（真查原始表），此处只验证内容服务的组装与
    排序分页逻辑，因此替换读取函数而不是伪造数据库。

    替身刻意复刻 `search_raw_hits` 的可观察契约：按**发布时间倒序**返回，
    并应用信源/时间窗/关键词过滤（关键词用子串匹配近似 SQL 的 `LIKE %kw%`）。
    替身在这些点上跑偏，排序与筛选相关的用例就失去意义了。
    """
    async def _search(*, keyword=None, match="text", start_time=None, end_time=None,
                      source_types=(), **_ignored):
        selected = list(hits)
        if source_types:
            allowed = {t.value if hasattr(t, "value") else str(t) for t in source_types}
            selected = [h for h in selected if h.source_type.value in allowed]
        if start_time is not None:
            selected = [h for h in selected if h.published_at >= start_time]
        if end_time is not None:
            selected = [h for h in selected if h.published_at <= end_time]
        if keyword:
            def _matched(hit) -> bool:
                content = contents.get(hit.content_uid)
                if content is None:
                    return False
                if match == "topic":
                    # 归属语义：匹配 source_keyword（真实现是双向模糊）
                    field = content.source_keyword or ""
                    return keyword in field or field in keyword
                return keyword in (content.text or "") or keyword in (content.title or "")
            selected = [h for h in selected if _matched(h)]

        return sorted(selected, key=lambda hit: hit.published_at, reverse=True)

    async def _load(uids, session):
        return {uid: contents[uid] for uid in uids if uid in contents}

    async def _assets(session, uids):
        return dict(assets or {})

    with ExitStack() as stack:
        # 凡是直接 `from ... import load_contents` 的模块都要单独打一次
        for module in ("app.services.content.service", "app.services.event.service",
                       "app.services.ticket.draft"):
            stack.enter_context(patch(f"{module}.load_contents", _load))
        # 候选筛选与多模态产物只在内容查询里用到
        stack.enter_context(patch("app.services.content.service.search_raw_hits", _search))
        stack.enter_context(patch("app.services.content.service.load_assets", _assets))
        yield


def make_user(username: str, password: str = "Passw0rd1", *,
              role: str = "analyst", status: str = "active",
              display_name: str = "", user_id: str | None = None,
              created_at: int | None = None) -> UserRecord:
    """构造一个用户记录（口令自动哈希）。"""
    return UserRecord(
        user_id=user_id or new_user_id(),
        username=username,
        password_hash=passwords.hash_password(password),
        display_name=display_name or username,
        role=role,
        status=status,
        created_at=created_at if created_at is not None else int(time.time()),
        last_login_at=None,
    )


class InMemoryUserRepository:
    """`UserRepository` 的内存实现。"""

    def __init__(self, users: list[UserRecord] | None = None) -> None:
        self._users: dict[str, UserRecord] = {u.user_id: u for u in (users or [])}

    # -- 读 --
    async def get_by_username(self, username: str) -> UserRecord | None:
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    async def get_by_user_id(self, user_id: str) -> UserRecord | None:
        return self._users.get(user_id)

    async def list_users(self, *, page: int, page_size: int, role: str | None = None,
                         status: str | None = None, keyword: str | None = None,
                         ) -> tuple[int, list[UserRecord]]:
        rows = list(self._users.values())
        if role:
            rows = [u for u in rows if u.role == role]
        if status:
            rows = [u for u in rows if u.status == status]
        if keyword:
            rows = [u for u in rows
                    if keyword in u.username or keyword in (u.display_name or "")]
        rows.sort(key=lambda u: u.created_at, reverse=True)
        total = len(rows)
        start = (page - 1) * page_size
        return total, rows[start:start + page_size]

    async def count_active_admins(self) -> int:
        return sum(1 for u in self._users.values()
                   if u.role == "admin" and u.status == "active")

    # -- 写 --
    async def create(self, record: UserRecord) -> UserRecord:
        if await self.get_by_username(record.username) is not None:
            raise ValueError(f"用户名重复：{record.username}（与唯一索引行为一致）")
        self._users[record.user_id] = record
        return record

    async def update(self, user_id: str, fields: dict) -> UserRecord | None:
        current = self._users.get(user_id)
        if current is None:
            return None
        updated = replace(current, **fields)
        self._users[user_id] = updated
        return updated

    # -- 测试用辅助 --
    def all_users(self) -> list[UserRecord]:
        return list(self._users.values())
