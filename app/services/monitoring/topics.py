"""监测主题与词包用例（FR-06~FR-09）。

**主题关键词（`topic.source_keyword`）是怎么来的**：原始数据里有一个 `source_keyword`
列，接入层按它做双向模糊匹配（`engines/ingestion/reader.py`）来决定内容属于哪个主题。
也就是说「新主题能不能召回到数据」取决于这个字段填得对不对，而使用者填的是词包、
并不了解原始数据的取值——所以在建主题时**从词包自动推导**：

    品牌词 → 事件词 → 竞品词 依次取权重最高的词；排除词不参与（它只负责收窄）

显式传入 `source_keyword` 时以传入值为准（老数据的关键词与词包不一致时会有用）。
词包后来被改而 `source_keyword` 仍是旧的推导值时会**跟随更新**——判断依据是
「当前值等于旧词包的推导值」，这样使用者显式写死的值不会被覆盖（见 `replace_keywords`）。

**同义词**：`topic_keyword` 没有同义词列，同一个词的多种写法就是多条词条
（「星耀」「星耀科技」同属品牌组），在召回上本就等价。
"""

import time

from loguru import logger

from app.errors import invalid_request, not_found
from app.security import ensure_data_scope
from app.services.audit import audit, note_change
from app.services.monitoring.repository import (
    KeywordRecord,
    MonitoringRepository,
    TopicBundle,
    TopicRecord,
    new_topic_id,
)
from engines.contracts.enums import AuditAction, KeywordGroup, TopicStatus, UserRole
from engines.contracts.permissions import Permission

__all__ = [
    "list_topics",
    "get_topic",
    "create_topic",
    "update_topic",
    "replace_keywords",
    "replace_channels",
    "derive_source_keyword",
    "normalize_keywords",
    "MAX_SOURCE_KEYWORD_LENGTH",
]

MAX_SOURCE_KEYWORD_LENGTH = 128

# 推导主题关键词时的分组优先级：品牌词最能代表监测对象，其次事件词，再次竞品词
_DERIVE_ORDER = (KeywordGroup.BRAND, KeywordGroup.EVENT, KeywordGroup.COMPETITOR)


# ---------------------------------------------------------------------------
# 词包处理
# ---------------------------------------------------------------------------

def normalize_keywords(items: list) -> list[KeywordRecord]:
    """校验并规整词包。

    两条规则，都针对「不报错但结果不对」的输入：

    1. **同组同词去重**（保留较大权重）：同一个词写两遍是输入冗余、不是语义冲突，
       直接报错会让前端一次误操作丢掉整份编辑；
    2. **跨组冲突直接拒绝**：同一个词既在召回组又在排除组，语义上自相矛盾
       （召回它又排除它），命中与否取决于匹配顺序——这种「看运气」的行为必须显式失败。
    """
    by_key: dict[tuple[str, str], KeywordRecord] = {}
    recall: dict[str, str] = {}       # 词 → 所属召回分组
    excluded: set[str] = set()

    for item in items:
        word = item.word.strip()
        if not word:
            raise invalid_request("词包中存在空词条")

        group = KeywordGroup(item.group_type).value
        key = (group, word)
        existing = by_key.get(key)
        if existing is None or item.weight > existing.weight:
            by_key[key] = KeywordRecord(word=word, group_type=group, weight=item.weight)
        else:
            logger.warning("词包重复词条已合并：group={} word={}", group, word)

        if group == KeywordGroup.EXCLUDE.value:
            excluded.add(word)
        else:
            recall[word] = group

    for word in sorted(excluded & recall.keys()):
        raise invalid_request(
            f"词「{word}」同时在{KeywordGroup(recall[word]).label}与排除词中",
            detail=f"word={word}")

    return list(by_key.values())


def derive_source_keyword(keywords: list[KeywordRecord]) -> str | None:
    """从词包推导主题关键词。没有召回类词条时返回 None。"""
    for group in _DERIVE_ORDER:
        candidates = [k for k in keywords if k.group_type == group.value]
        if candidates:
            best = max(candidates, key=lambda k: k.weight)
            return best.word[:MAX_SOURCE_KEYWORD_LENGTH]
    return None


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------

def _bundle(repo_keywords: list[KeywordRecord], topic: TopicRecord,
            channel_ids: list[str]) -> TopicBundle:
    return TopicBundle(topic=topic, keywords=repo_keywords, channel_ids=channel_ids)


async def _load_bundle(repo: MonitoringRepository, topic: TopicRecord) -> TopicBundle:
    keywords = await repo.list_keywords(topic.topic_id)
    channel_ids = await repo.list_topic_channel_ids(topic.topic_id)
    return _bundle(keywords, topic, channel_ids)


async def _require_topic(repo: MonitoringRepository, topic_id: str) -> TopicRecord:
    topic = await repo.get_topic(topic_id)
    if topic is None:
        raise not_found(f"主题不存在：{topic_id}", detail=f"topic_id={topic_id}")
    return topic


def _snapshot(topic: TopicRecord, keywords: list[KeywordRecord],
              channel_ids: list[str]) -> dict:
    """审计用的主题快照（词包只记数量与分组，避免把整份词包塞进审计行）。"""
    groups: dict[str, int] = {}
    for keyword in keywords:
        groups[keyword.group_type] = groups.get(keyword.group_type, 0) + 1
    return {
        "name": topic.name,
        "source_keyword": topic.source_keyword,
        "owner_id": topic.owner_id,
        "status": topic.status,
        "start_at": topic.start_at,
        "keyword_count": len(keywords),
        "keyword_groups": groups,
        "channel_ids": sorted(channel_ids),
    }


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

async def list_topics(repo: MonitoringRepository, *, page: int = 1, page_size: int = 20,
                      status: str | None = None, owner_id: str | None = None,
                      keyword: str | None = None,
                      ) -> tuple[int, list[TopicBundle]]:
    """主题列表。词包按分组统计返回，不返回全部词条（可能上百条）。"""
    total, topics = await repo.list_topics(page=page, page_size=page_size,
                                           status=status, owner_id=owner_id,
                                           keyword=keyword)
    keywords_map = await repo.list_keywords_for([t.topic_id for t in topics])
    channel_map = {t.topic_id: await repo.list_topic_channel_ids(t.topic_id)
                   for t in topics}
    bundles = [_bundle(keywords_map.get(t.topic_id, []), t, channel_map[t.topic_id])
               for t in topics]
    return total, bundles


async def get_topic(repo: MonitoringRepository, topic_id: str) -> TopicBundle:
    return await _load_bundle(repo, await _require_topic(repo, topic_id))


@audit(AuditAction.CONFIG_UPDATE, target="topic:{name}")
async def create_topic(repo: MonitoringRepository, *, actor: object, name: str,
                       description: str = "", source_keyword: str | None = None,
                       owner_id: str | None = None, start_at: int | None = None,
                       status: str = TopicStatus.ENABLED.value,
                       keywords: list | None = None,
                       channel_ids: list[str] | None = None) -> TopicBundle:
    """新建主题（含词包与通道关联）。

    `owner_id` 只有管理员能指定他人；业务角色（分析师/公关）建的主题一律归自己
    ——数据范围判定以 owner 为准，允许指定 owner 等于允许把主题挂到别人名下。
    """
    keyword_records = normalize_keywords(keywords or [])
    resolved_keyword = (source_keyword or "").strip() or derive_source_keyword(keyword_records)

    if not resolved_keyword:
        logger.warning("主题「{}」既没有召回类词条也没有主题关键词，接入时不会召回到任何内容", name)

    owner = (owner_id or "").strip() or actor.user_id
    if owner != actor.user_id and actor.role != UserRole.ADMIN.value:
        raise invalid_request("只有管理员可以把主题指定给其他负责人")

    record = TopicRecord(
        topic_id=new_topic_id(),
        name=name.strip(),
        description=(description or "").strip(),
        source_keyword=resolved_keyword,
        owner_id=owner,
        status=TopicStatus(status).value,
        start_at=start_at,
        created_at=int(time.time()),
    )
    created = await repo.create_topic(record)
    if keyword_records:
        await repo.replace_keywords(created.topic_id, keyword_records)
    if channel_ids:
        await _validate_channel_ids(repo, channel_ids)
        await repo.replace_topic_channels(created.topic_id, channel_ids)

    bundle = await _load_bundle(repo, created)
    note_change(after=_snapshot(bundle.topic, bundle.keywords, bundle.channel_ids))
    logger.info("新建主题 topic_id={} name={} owner={} 词条 {} 条",
                created.topic_id, created.name, created.owner_id, len(keyword_records))
    return bundle


@audit(AuditAction.CONFIG_UPDATE, target="topic:{topic_id}")
async def update_topic(repo: MonitoringRepository, *, actor: object, topic_id: str,
                       name: str | None = None, description: str | None = None,
                       source_keyword: str | None = None, start_at: int | None = None,
                       status: str | None = None) -> TopicBundle:
    """更新主题元信息。**词包与通道关联不在这里**（各走各的端点，见模块说明）。"""
    topic = await _require_topic(repo, topic_id)
    ensure_data_scope(actor, Permission.TOPIC_WRITE, topic.owner_id)

    before = _snapshot(topic, await repo.list_keywords(topic_id),
                       await repo.list_topic_channel_ids(topic_id))

    fields: dict = {}
    if name is not None:
        if not name.strip():
            raise invalid_request("主题名称不能为空")
        fields["name"] = name.strip()[:128]
    if description is not None:
        fields["description"] = description.strip()
    if source_keyword is not None:
        fields["source_keyword"] = source_keyword.strip()[:MAX_SOURCE_KEYWORD_LENGTH] or None
    if start_at is not None:
        fields["start_at"] = start_at
    if status is not None:
        fields["status"] = TopicStatus(status).value

    if not fields:
        return await _load_bundle(repo, topic)

    updated = await repo.update_topic(topic_id, fields)
    if updated is None:
        raise not_found(f"主题不存在：{topic_id}")

    bundle = await _load_bundle(repo, updated)
    note_change(before=before, after=_snapshot(bundle.topic, bundle.keywords,
                                               bundle.channel_ids))
    logger.info("更新主题 topic_id={} 字段={}", topic_id, sorted(fields))
    return bundle


@audit(AuditAction.CONFIG_UPDATE, target="topic:{topic_id}")
async def replace_keywords(repo: MonitoringRepository, *, actor: object, topic_id: str,
                           keywords: list) -> TopicBundle:
    """全量替换词包（FR-07）。

    **主题关键词的跟随规则**：若当前 `source_keyword` 等于旧词包推导出的值
    （说明它是自动来的，或主题建时就没有词包），替换后按新词包重新推导；
    若使用者显式写死过（与旧推导值不同），则不动它——那是有意为之的取值。
    """
    topic = await _require_topic(repo, topic_id)
    ensure_data_scope(actor, Permission.TOPIC_WRITE, topic.owner_id)

    old_keywords = await repo.list_keywords(topic_id)
    before = _snapshot(topic, old_keywords, await repo.list_topic_channel_ids(topic_id))

    keyword_records = normalize_keywords(keywords)
    await repo.replace_keywords(topic_id, keyword_records)

    old_derived = derive_source_keyword(old_keywords)
    if topic.source_keyword in (None, old_derived):
        new_derived = derive_source_keyword(keyword_records)
        if new_derived != topic.source_keyword:
            updated = await repo.update_topic(topic_id, {"source_keyword": new_derived})
            if updated:
                topic = updated
            logger.info("主题关键词随词包更新 topic_id={} {} → {}",
                        topic_id, old_derived, new_derived)

    bundle = await _load_bundle(repo, topic)
    note_change(before=before, after=_snapshot(bundle.topic, bundle.keywords,
                                               bundle.channel_ids))
    logger.info("替换词包 topic_id={} 词条 {} 条", topic_id, len(keyword_records))
    return bundle


@audit(AuditAction.CONFIG_UPDATE, target="topic:{topic_id}")
async def replace_channels(repo: MonitoringRepository, *, actor: object, topic_id: str,
                           channel_ids: list[str]) -> TopicBundle:
    """全量替换主题启用的信源通道（FR-09）。"""
    topic = await _require_topic(repo, topic_id)
    ensure_data_scope(actor, Permission.TOPIC_WRITE, topic.owner_id)

    before = _snapshot(topic, await repo.list_keywords(topic_id),
                       await repo.list_topic_channel_ids(topic_id))

    await _validate_channel_ids(repo, channel_ids)
    await repo.replace_topic_channels(topic_id, channel_ids)

    bundle = await _load_bundle(repo, topic)
    note_change(before=before, after=_snapshot(bundle.topic, bundle.keywords,
                                               bundle.channel_ids))
    logger.info("替换主题通道 topic_id={} 通道 {} 个", topic_id, len(bundle.channel_ids))
    return bundle


async def _validate_channel_ids(repo: MonitoringRepository, channel_ids: list[str]) -> None:
    """通道必须已存在——写错一个 id 就整批拒绝，避免留下指向不存在通道的关联。"""
    known = {c.channel_id for c in await repo.list_channels()}
    unknown = [cid for cid in dict.fromkeys(channel_ids) if cid not in known]
    if unknown:
        raise invalid_request("存在未知的信源通道",
                              detail=f"unknown={', '.join(unknown)}")
