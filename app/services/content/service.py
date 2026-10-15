"""内容查询用例（FR-42~FR-44）。

**两段查询**（设计规格 ADR-03）：标签在分析区（`content_analysis`），
正文/发布时间/账号在原始表（四张异构表）。跨异构表 JOIN 无法用一条 SQL 表达，
所以是「先定候选、再取文案」：

    原始侧条件（时间窗、关键词） → 候选 content_uid（按发布时间倒序）
    → 在分析区叠筛选（主题/情感/风险/水军/人工复核）
    → 取本页正文并回填作者与互动量

**排序统一为「发布时间倒序」**，因为舆情流的阅读方式是「最新发生了什么」。
代价是分页在应用层完成（候选集先由原始表给出），演示规模（千级）可以接受；
数据量继续增长时应当把 `published_at` 冗余进 `content_analysis`（需要改 DDL），
恢复纯 SQL 分页——写在这里是为了让后来者知道这是个有意识的取舍，而不是疏漏。

时间窗筛的是**内容的发布时间**，不是打标时间：用户说的「近 24 小时」
指的是舆情什么时候发生的。
"""

import json

from loguru import logger

from app.errors import not_found
from app.schemas.content import LIST_TEXT_LIMIT
from app.services.content.repository import AnalysisRecord, ContentRepository
from engines.contracts.content import Author, Engagement, UnifiedContent
from engines.contracts.enums import SourceType
from engines.ingestion.modality.repository import load_assets
from engines.ingestion.reader import RawHit, load_contents, search_raw_hits

__all__ = [
    "list_contents",
    "get_content_detail",
    "build_item",
    "build_detail",
    "author_view",
    "engagement_view",
    "truncate_text",
    "display_text",
]


def engagement_view(engagement: Engagement) -> dict:
    return {
        "likes": engagement.likes,
        "comments": engagement.comments,
        "shares": engagement.shares,
        "collects": engagement.collects,
        "replies": engagement.replies,
        "total": engagement.total,
    }


def author_view(author: Author) -> dict:
    return {
        "account_id": author.account_id,
        "nickname": author.nickname,
        "follower_count": author.follower_count,
        "verified_type": author.verified_type,
        "influence_score": author.influence_score,
        "is_kol": author.is_kol,
        "ip_location": author.ip_location,
    }


def truncate_text(text: str, limit: int = LIST_TEXT_LIMIT) -> tuple[str, bool]:
    """列表正文截断。返回 (文本, 是否被截断)——标记出来，避免前端以为内容只有这么长。"""
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def display_text(content: UnifiedContent) -> str:
    """展示文本：正文为空时回落到标题（论坛/新闻的短帖会这样）。"""
    return content.raw_text or content.display_text


def build_item(record: AnalysisRecord, content: UnifiedContent, *,
               topic_name: str = "") -> dict:
    """把「分析标签 + 原始内容」组装成列表条目。"""
    text, truncated = truncate_text(display_text(content))
    source_type = SourceType(record.source_type)

    return {
        "content_uid": record.content_uid,
        "source_type": source_type.value,
        "platform_label": source_type.label,
        "record_type": content.record_type,
        "title": content.title,
        "text": text,
        "text_truncated": truncated,
        "url": content.url,
        "published_at": content.published_at,
        "author": author_view(content.author),
        "engagement": engagement_view(content.engagement),
        "topic_id": record.topic_id,
        "topic_name": topic_name,
        "sentiment": record.sentiment,
        "emotion": record.emotion,
        "sentiment_confidence": record.sentiment_confidence,
        "risk_level": record.risk_level,
        "intensity_index": record.intensity_index,
        "is_manual_override": record.is_manual_override,
        "is_suspected_bot": record.is_suspected_bot,
    }


def build_detail(record: AnalysisRecord, content: UnifiedContent, *, topic_name: str = "",
                 assets: list | None = None) -> dict:
    """详情 = 列表条目 + 全文 + 判定依据 + 多模态证据（FR-44）。"""
    detail = build_item(record, content, topic_name=topic_name)
    detail.update({
        "text": display_text(content),          # 详情给全文
        "text_truncated": False,
        "raw_text": content.raw_text,
        "source_keyword": content.source_keyword,
        "parent_content_uid": content.parent_content_uid,
        "risk_factors": _parse_json(record.risk_factors),
        "override_reason": record.override_reason,
        "model_version": record.model_version or "",
        "simhash": record.simhash or "",
        "analyzed_at": record.analyzed_at,
        "modality_assets": [
            {
                "asset_id": asset.asset_id,
                "asset_type": asset.asset_type,
                "parser": asset.parser,
                "parse_status": asset.parse_status,
                "parsed_text": asset.parsed_text,
                "error_message": asset.error_message,
                "origin_url": asset.origin_url,
            }
            for asset in (assets or [])
        ],
    })
    return detail


def _parse_json(raw: str | None) -> dict | None:
    """`risk_factors` 存的是 JSON 文本；损坏时返回 None 而不是让详情 500。"""
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("risk_factors 不是合法 JSON，已忽略：{}", raw[:80])
        return None
    return parsed if isinstance(parsed, dict) else {"value": parsed}


async def list_contents(repo: ContentRepository, session, *, page: int = 1,
                        page_size: int = 20, topic_id: str | None = None,
                        source_type: str | None = None, sentiment: str | None = None,
                        risk_level: str | None = None, start_time: int | None = None,
                        end_time: int | None = None, keyword: str | None = None,
                        is_suspected_bot: bool | None = None,
                        is_manual_override: bool | None = None,
                        ) -> tuple[int, list[dict]]:
    """多维筛选 + 分页（FR-42/43）。返回 (命中总数, 本页条目)。"""
    source_types = (SourceType(source_type),) if source_type else ()

    hits: list[RawHit] = await search_raw_hits(
        session=session, keyword=keyword, start_time=start_time, end_time=end_time,
        source_types=source_types)
    if not hits:
        return 0, []

    order = {hit.content_uid: index for index, hit in enumerate(hits)}
    records = await repo.query_analysis(
        [hit.content_uid for hit in hits],
        topic_id=topic_id, sentiment=sentiment, risk_level=risk_level,
        is_suspected_bot=is_suspected_bot, is_manual_override=is_manual_override)

    # 排序依据是原始表的发布时间：候选列表已经是倒序，这里按它复原顺序
    records.sort(key=lambda record: order.get(record.content_uid, len(order)))
    total = len(records)

    start = (page - 1) * page_size
    page_records = records[start:start + page_size]
    if not page_records:
        return total, []

    contents = await load_contents([r.content_uid for r in page_records], session)
    names = await repo.topic_names([r.topic_id for r in page_records if r.topic_id])

    items: list[dict] = []
    for record in page_records:
        content = contents.get(record.content_uid)
        if content is None:
            # 分析表里有标签、原始表里已没有这条内容（数据被清理过）。
            # 少一条比让整个列表报错好，但必须留痕，否则「总数对不上」无从排查。
            logger.warning("内容标签存在但原始内容缺失，已跳过：{}", record.content_uid)
            continue
        items.append(build_item(record, content,
                                topic_name=names.get(record.topic_id or "", "")))

    return total, items


async def get_content_detail(repo: ContentRepository, session,
                             content_uid: str) -> dict:
    """内容详情：原文 + 多模态证据 + 判定依据（FR-44）。"""
    record = await repo.get_analysis(content_uid)
    if record is None:
        raise not_found(f"内容尚未完成分析或不存在：{content_uid}",
                        detail=f"content_uid={content_uid}")

    contents = await load_contents([content_uid], session)
    content = contents.get(content_uid)
    if content is None:
        raise not_found(f"原始内容不存在：{content_uid}",
                        detail=f"content_uid={content_uid}")

    assets = (await load_assets(session, [content_uid])).get(content_uid, [])
    names = await repo.topic_names([record.topic_id] if record.topic_id else [])

    return build_detail(record, content, topic_name=names.get(record.topic_id or "", ""),
                        assets=assets)
