"""事件专题、传播溯源、观点聚类用例（FR-45~FR-48）。

**本模块全部是读取**，没有写入、没有审计——事件与标签由 analytics 产出，
这里只负责把「事件 → 内容 → 标签 → 原文」拼成前端能直接渲染的结构。

三处需要说明的设计：

1. **事件统计现算**（`collect_stats`）。`event` 表的 `total_volume` /
   `negative_ratio` / `first_seen_at` / `last_seen_at` / `risk_level` 都是空的
   （T-11 建档时没算），而它们全部可由关联内容推出。维护一份会漂移的缓存不如现算，
   口径也写进了响应字段说明。
2. **溯源节点角色是推导出来的**（`derive_roles`），不是人工标注：
   `spread_node` 表为空，且节点角色本就可以由「发布时间 + 账号认证与影响力 + 信源类型」
   判定。响应里的 `role_source="derived"` 明确标注了这一点，避免让人以为有人工结论。
3. **时间线默认只给关键节点**。一个事件上千条内容，全铺出来不是时间线而是流水账；
   `node_scope=all` 可以取全量，默认 `key` 保留首发/引爆/回应/跟进与高权重扩散节点。
"""

from loguru import logger

from app.errors import invalid_request, not_found
from app.services.audit import audit, note_change
from app.services.content.repository import AnalysisRecord, ContentRepository
from app.services.content.service import (
    author_view,
    build_item,
    display_text,
    engagement_view,
    truncate_text,
)
from app.services.event.repository import ClusterRecord, EventRecord, EventRepository
from engines.contracts.content import UnifiedContent
from engines.contracts.enums import AuditAction, ContentRole, RiskLevel, Sentiment, SourceType
from engines.ingestion.reader import load_contents

__all__ = [
    "list_events",
    "get_event_detail",
    "override_risk_level",
    "build_timeline",
    "list_clusters",
    "list_cluster_members",
    "collect_stats",
    "derive_roles",
    "KEY_NODE_TEXT_LIMIT",
    "GRAPH_RELATION_LIMIT",
    "MIN_OVERRIDE_REASON",
]

# 人工复核理由的最短长度。理由是这个功能的核心产物——没有理由的等级变更无法复盘
MIN_OVERRIDE_REASON = 5

# 时间线节点与簇代表言论的正文长度（比列表的 200 字更短：它们是卡片流里的摘要）
KEY_NODE_TEXT_LIMIT = 120

# 图谱默认关系条数上限（全量返回会让前端图变成一团毛线）
GRAPH_RELATION_LIMIT = 60

# 判定「官方回应」的认证类型；「媒体」归入跟进（它是报道方，不是当事机构）
_OFFICIAL_VERIFIED = ("官方", "政务")
_MEDIA_VERIFIED = ("媒体",)

# 关键扩散节点：扩散内容里互动量最高的若干条（按比例取，并设上下限）。
# 比例 5% + 上限 50 是两个约束共同作用的结果：一个事件上千条内容时，
# 「前 20%」就是两百多个节点，那条时间线已经没法看了；而下限 1 条保证
# 小事件（只有几条内容）也能标出至少一个扩散节点。
_SPREAD_TOP_RATIO = 0.05
_SPREAD_TOP_MIN = 1
_SPREAD_TOP_MAX = 50


# ---------------------------------------------------------------------------
# 角色推导
# ---------------------------------------------------------------------------

def derive_roles(contents: list[UnifiedContent]) -> dict[str, ContentRole]:
    """由内容自身与账号权重推导传播角色。

    规则（顺序即优先级）：

    | 角色 | 判定 |
    |------|------|
    | `origin` 首发 | **内容主体**中发布时间最早的那条 |
    | `official` 官方回应 | 作者认证为「官方 / 政务」的内容主体 |
    | `ignition` 引爆 | 其余内容主体里账号影响力最高、且达到 KOL 阈值的**一条** |
    | `followup` 跟进 | 新闻门户的内容主体，或作者认证为「媒体」 |
    | `spread` 扩散 | 其余全部（含评论） |

    只让**内容主体**（非评论）参与前四类判定：评论是内容的下游反应，
    把它们算成「首发」或「引爆」会让溯源结论失真——一条热度很高的评论不是事件的起点。
    """
    posts = [c for c in contents if c.record_type != "comment"]
    roles: dict[str, ContentRole] = {c.content_uid: ContentRole.SPREAD for c in contents}
    if not posts:
        return roles

    # 官方回应先标，避免被后续的「引爆」抢走（官方账号影响力通常也很高）
    official_uids = {c.content_uid for c in posts
                     if c.author.verified_type in _OFFICIAL_VERIFIED}
    for uid in official_uids:
        roles[uid] = ContentRole.OFFICIAL

    # 首发：内容主体中最早的（含官方账号先发声明的情况——那就是首发）
    origin = min(posts, key=lambda c: (c.published_at, c.content_uid))
    roles[origin.content_uid] = ContentRole.ORIGIN

    # 引爆：剩下里影响力最高的 KOL
    candidates = [c for c in posts
                  if c.content_uid not in official_uids
                  and c.content_uid != origin.content_uid]
    kol = [c for c in candidates if c.author.is_kol]
    if kol:
        ignition = max(kol, key=lambda c: (c.author.influence_score, -c.published_at))
        roles[ignition.content_uid] = ContentRole.IGNITION

    for content in posts:
        if roles[content.content_uid] in (ContentRole.ORIGIN, ContentRole.OFFICIAL,
                                          ContentRole.IGNITION):
            continue
        if content.source_type is SourceType.NEWS or \
                content.author.verified_type in _MEDIA_VERIFIED:
            roles[content.content_uid] = ContentRole.FOLLOWUP

    return roles


def _key_node_uids(contents: list[UnifiedContent],
                   roles: dict[str, ContentRole]) -> set[str]:
    """关键节点：非扩散角色（首发/引爆/回应/跟进），或扩散里互动量最高的若干条。"""
    key = {uid for uid, role in roles.items() if role is not ContentRole.SPREAD}
    spread = [c for c in contents if roles.get(c.content_uid) is ContentRole.SPREAD]
    if spread:
        spread.sort(key=lambda c: c.engagement.total, reverse=True)
        top = min(_SPREAD_TOP_MAX,
                  max(_SPREAD_TOP_MIN, int(len(spread) * _SPREAD_TOP_RATIO)))
        key.update(c.content_uid for c in spread[:top]
                   if c.engagement.total > 0 or c.author.is_kol)
    return key


# ---------------------------------------------------------------------------
# 统计聚合
# ---------------------------------------------------------------------------

def collect_stats(contents: list[UnifiedContent],
                  records: list[AnalysisRecord]) -> dict:
    """由关联内容聚合事件统计（口径见 `EventStatsView` 的字段说明）。"""
    by_uid = {content.content_uid: content for content in contents}
    published = [c.published_at for c in contents if c.published_at]
    sentiments = {"positive": 0, "neutral": 0, "negative": 0}
    sources: dict[str, int] = {}
    severity = 0
    intensity_values: list[float] = []
    negative = 0

    for record in records:
        sentiments[record.sentiment] = sentiments.get(record.sentiment, 0) + 1
        if record.sentiment == Sentiment.NEGATIVE.value:
            negative += 1
        sources[record.source_type] = sources.get(record.source_type, 0) + 1
        if record.risk_level:
            severity = max(severity, RiskLevel(record.risk_level).severity)
        if record.intensity_index is not None:
            intensity_values.append(record.intensity_index)

    total_volume = sum(by_uid[r.content_uid].engagement.total
                       for r in records if r.content_uid in by_uid)

    return {
        "content_count": len(contents),
        "post_count": sum(1 for c in contents if c.record_type != "comment"),
        "total_volume": total_volume,
        "negative_count": negative,
        "negative_ratio": (round(negative / len(records), 4) if records else None),
        "risk_level": _severity_to_level(severity),
        "intensity_index": (round(sum(intensity_values) / len(intensity_values), 4)
                            if intensity_values else None),
        "first_seen_at": min(published) if published else None,
        "last_seen_at": max(published) if published else None,
        "source_distribution": sources,
        "sentiment_distribution": sentiments,
    }


def _severity_to_level(severity: int) -> str | None:
    for level in RiskLevel:
        if level.severity == severity:
            return level.value
    return None


async def _load_event_bundle(repo: EventRepository, content_repo: ContentRepository,
                             session, event: EventRecord) -> tuple[
                                 dict[str, UnifiedContent], dict[str, AnalysisRecord]]:
    """取事件的原文与标签（统计、时间线、聚类三处共用一套组装）。"""
    uid_map = await repo.content_uids([event.event_id])
    uids = uid_map.get(event.event_id, [])
    if not uids:
        return {}, {}

    contents = await load_contents(uids, session)
    records = await content_repo.query_analysis(list(contents.keys()))
    return contents, {r.content_uid: r for r in records}


def _topic_of_contents(records: dict[str, AnalysisRecord]) -> dict[str, int]:
    """按内容归属统计主题票数——事件的归属主题取票数最多的那个。"""
    votes: dict[str, int] = {}
    for record in records.values():
        if record.topic_id:
            votes[record.topic_id] = votes.get(record.topic_id, 0) + 1
    return votes


async def _summarize(repo: EventRepository, content_repo: ContentRepository, session,
                     event: EventRecord, *, cluster_counts: dict[str, int],
                     ) -> dict:
    contents, records_by_uid = await _load_event_bundle(repo, content_repo, session, event)
    stats = collect_stats(list(contents.values()), list(records_by_uid.values()))
    algorithm_level = stats["risk_level"]

    votes = _topic_of_contents(records_by_uid)
    dominant_topic = max(votes, key=lambda k: votes[k]) if votes else None
    topic_names = await content_repo.topic_names([dominant_topic] if dominant_topic else [])

    # FR-24：人工复核过就展示人工值，同时把算法结论一并给出（双份记录）。
    # 事件可能既没有内容也没有人工复核，此时两级都是 null。
    effective = event.risk_level if event.manual_override else algorithm_level
    stats["risk_level"] = effective

    return {
        "event_id": event.event_id,
        "title": event.title,
        "status": event.status,
        "topic_id": dominant_topic,
        "topic_name": topic_names.get(dominant_topic or "", ""),
        "cluster_count": cluster_counts.get(event.event_id, 0),
        "risk_level": effective,
        "algorithm_risk_level": algorithm_level,
        "is_manual_override": event.manual_override,
        "override_reason": event.override_reason,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

async def list_events(repo: EventRepository, content_repo: ContentRepository, session, *,
                      topic_id: str | None = None, risk_level: str | None = None,
                      status: str | None = None, keyword: str | None = None,
                      page: int = 1, page_size: int = 20,
                      ) -> tuple[int, list[dict]]:
    """事件专题列表（FR-48）。

    风险等级按**关联内容的最高风险**过滤与展示——`event.risk_level` 列为空，
    现算出来的值才是真实风险。排序用现算的最近发布时间倒序（活跃的事件在前），
    因此分页在应用层完成（事件数量远小于内容，代价可忽略）。
    """
    events = await repo.list_events(topic_id=topic_id, status=status, keyword=keyword)
    if not events:
        return 0, []

    cluster_counts = await repo.cluster_counts([e.event_id for e in events])
    summaries: list[dict] = []
    for event in events:
        summary = await _summarize(repo, content_repo, session, event,
                                   cluster_counts=cluster_counts)
        if risk_level and summary["stats"].get("risk_level") != risk_level:
            continue
        summaries.append(summary)

    summaries.sort(key=lambda s: (s["stats"].get("last_seen_at") or 0,
                                  s["stats"]["content_count"]), reverse=True)
    total = len(summaries)
    start = (page - 1) * page_size
    return total, summaries[start:start + page_size]


@audit(AuditAction.RISK_OVERRIDE, target="event:{event_id}")
async def override_risk_level(repo: EventRepository, content_repo: ContentRepository,
                              session, *, actor: object, event_id: str,
                              risk_level: str, reason: str) -> dict:
    """人工复核事件风险等级（FR-24）。

    三条业务约束：

    1. **必须填写理由**（≥5 字）：没有理由的等级变更无法复盘，等于把结论变成了意见；
    2. **重复提交同一等级不写库**：`event` 表只在等级真的变化时更新，
       理由也保持首次填写的那份（后一次确认不改写历史理由）；
    3. **算法原始结论始终可查**：表里存人工值，算法值每次现算，两者在响应里同时给出。

    关于第 2 点的边界：**这次「确认」动作本身仍然进审计**（before 与 after 相同），
    因为审计的语义是「谁在什么时候做了什么」，不因结果是否为变更而豁免——
    给这个端点开特例会让「审计表里有什么」变得不可预测。

    权限是 `risk:override`（**仅分析师**，2026-10-15 确认）——管理员不参与业务结论的修改。
    """
    event = await repo.get_event(event_id)
    if event is None:
        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    reason = (reason or "").strip()
    if len(reason) < MIN_OVERRIDE_REASON:
        raise invalid_request(f"复核理由至少 {MIN_OVERRIDE_REASON} 个字",
                              detail=f"got={len(reason)}")

    before = await _summarize(repo, content_repo, session, event, cluster_counts={})
    algorithm_level = before["algorithm_risk_level"]

    if risk_level == before["risk_level"] and event.manual_override:
        # 复核成同一个等级：不写 event 表（历史理由与时间不被改写），响应照常返回完整详情。
        # 审计仍会记下这次确认动作（before 与 after 相同），见函数文档第 2 点的边界说明。
        logger.info("事件 {} 风险等级未变化（{}），跳过复核写库", event_id, risk_level)
        detail = dict(before)
        entity_count, relation_count = await repo.entity_counts(event_id)
        detail.update(entity_count=entity_count, relation_count=relation_count,
                      cluster_count=(await repo.cluster_counts([event_id])).get(event_id, 0))
        note_change(
            before={"risk_level": before["risk_level"],
                    "algorithm_risk_level": algorithm_level,
                    "is_manual_override": True},
            after={"risk_level": before["risk_level"],
                   "algorithm_risk_level": algorithm_level,
                   "is_manual_override": True,
                   "reason": event.override_reason or "",
                   "changed": False},
        )
        return detail

    updated = await repo.apply_risk_override(event_id, risk_level=risk_level, reason=reason)
    if updated is None:
        raise not_found(f"事件不存在：{event_id}")

    detail = await _summarize(repo, content_repo, session, updated,
                              cluster_counts=await repo.cluster_counts([event_id]))
    entity_count, relation_count = await repo.entity_counts(event_id)
    detail["entity_count"] = entity_count
    detail["relation_count"] = relation_count

    note_change(
        before={"risk_level": before["risk_level"],
                "algorithm_risk_level": algorithm_level,
                "is_manual_override": event.manual_override},
        after={"risk_level": risk_level,
               "algorithm_risk_level": algorithm_level,
               "is_manual_override": True,
               "reason": reason},
    )
    logger.info("人工复核事件风险 event_id={} 算法={} 人工={} 操作人={}",
                event_id, algorithm_level, risk_level, getattr(actor, "user_id", "-"))
    return detail


async def get_event_detail(repo: EventRepository, content_repo: ContentRepository,
                           session, event_id: str) -> dict:
    """事件详情：统计 + 聚类数 + 图谱规模。"""
    event = await repo.get_event(event_id)
    if event is None:
        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    cluster_counts = await repo.cluster_counts([event_id])
    detail = await _summarize(repo, content_repo, session, event,
                             cluster_counts=cluster_counts)
    entity_count, relation_count = await repo.entity_counts(event_id)
    detail["entity_count"] = entity_count
    detail["relation_count"] = relation_count
    return detail


async def build_timeline(repo: EventRepository, content_repo: ContentRepository, session,
                         event_id: str, *, node_scope: str = "key") -> dict:
    """传播溯源时间线（FR-45）：按发布时间**正序**，标注首发与关键扩散节点。"""
    if node_scope not in ("key", "all"):
        from app.errors import invalid_request

        raise invalid_request("node_scope 只能是 key 或 all")

    event = await repo.get_event(event_id)
    if event is None:
        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    contents, records_by_uid = await _load_event_bundle(repo, content_repo, session, event)
    if not contents:
        return {"event_id": event_id, "node_scope": node_scope, "ordered_by": "published_at",
                "role_source": "derived", "total_contents": 0, "returned": 0,
                "role_summary": {}, "nodes": []}

    ordered = sorted(contents.values(), key=lambda c: (c.published_at, c.content_uid))
    roles = derive_roles(ordered)
    key_uids = _key_node_uids(ordered, roles)
    selected = [c for c in ordered if node_scope == "all" or c.content_uid in key_uids]

    role_summary: dict[str, int] = {}
    for content in selected:
        role = roles[content.content_uid].value
        role_summary[role] = role_summary.get(role, 0) + 1

    nodes: list[dict] = []
    for content in selected:
        # 未打标的内容也要出现：时间线讲的是**传播结构**（谁在什么时候发了什么），
        # 情感与风险只是附注。因为缺标签就把节点丢掉，可能把「首发」整条抹掉。
        record = records_by_uid.get(content.content_uid)
        if record is None:
            logger.warning("事件 {} 的内容缺少分析标签，按未标注展示：{}",
                           event_id, content.content_uid)

        text, truncated = truncate_text(display_text(content), KEY_NODE_TEXT_LIMIT)
        source_type = SourceType((record.source_type if record else content.source_type.value))
        nodes.append({
            "content_uid": content.content_uid,
            "published_at": content.published_at,
            "role": roles[content.content_uid].value,
            "source_type": source_type.value,
            "platform_label": source_type.label,
            "record_type": content.record_type,
            "author": author_view(content.author),
            "title": content.title,
            "text": text,
            "text_truncated": truncated,
            "url": content.url,
            "engagement": engagement_view(content.engagement),
            "sentiment": record.sentiment if record else None,
            "risk_level": record.risk_level if record else None,
            "is_key_node": content.content_uid in key_uids,
        })

    return {
        "event_id": event_id,
        "node_scope": node_scope,
        "ordered_by": "published_at",
        "role_source": "derived",
        "total_contents": len(contents),
        "returned": len(nodes),
        "role_summary": role_summary,
        "nodes": nodes,
    }


async def list_clusters(repo: EventRepository, content_repo: ContentRepository, session,
                        event_id: str) -> dict:
    """观点聚类（FR-47）：按簇规模倒序，附代表性言论。"""
    event = await repo.get_event(event_id)
    if event is None:
        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    clusters = await repo.list_clusters(event_id)
    items: list[dict] = []
    for cluster in clusters:
        reps = await _representatives(repo, content_repo, session, cluster)
        items.append({
            "cluster_id": cluster.cluster_id,
            "label": cluster.label,
            "summary": cluster.summary,
            "size": cluster.size,
            "sentiment": cluster.sentiment,
            "intent": cluster.intent,
            "representatives": reps,
        })

    return {"cluster_id_scope": "event", "event_id": event_id,
            "total": len(items), "items": items}


async def _representatives(repo: EventRepository, content_repo: ContentRepository, session,
                           cluster: ClusterRecord, *, limit: int = 3) -> list[dict]:
    """取簇的代表性言论（落库时标记的成员），组装成与动态流一致的条目。"""
    _, uids = await repo.cluster_members(cluster.cluster_id, page=1, page_size=limit,
                                        representatives_only=True)
    if not uids:
        return []

    contents = await load_contents(uids, session)
    records = await content_repo.query_analysis(uids)
    topic_names = await content_repo.topic_names(
        [r.topic_id for r in records if r.topic_id])

    items: list[dict] = []
    for record in records:
        content = contents.get(record.content_uid)
        if content is None:
            continue
        items.append(build_item(record, content,
                                topic_name=topic_names.get(record.topic_id or "", "")))
    return items


async def list_cluster_members(repo: EventRepository, content_repo: ContentRepository,
                               session, cluster_id: str, *, page: int = 1,
                               page_size: int = 20) -> tuple[int, list[dict]]:
    """簇成员下钻（FR-47 的「点击簇可下钻至成员内容」）。

    返回结构与舆情动态流一致——前端可以直接复用同一张卡片与详情跳转。
    """
    cluster = await repo.get_cluster(cluster_id)
    if cluster is None:
        raise not_found(f"观点簇不存在：{cluster_id}", detail=f"cluster_id={cluster_id}")

    total, uids = await repo.cluster_members(cluster_id, page=page, page_size=page_size)
    if not uids:
        return total, []

    contents = await load_contents(uids, session)
    records = await content_repo.query_analysis(uids)
    topic_names = await content_repo.topic_names(
        [r.topic_id for r in records if r.topic_id])

    items: list[dict] = []
    for record in records:
        content = contents.get(record.content_uid)
        if content is None:
            logger.warning("簇成员缺少原始内容：{}", record.content_uid)
            continue
        items.append(build_item(record, content,
                                topic_name=topic_names.get(record.topic_id or "", "")))
    return total, items
