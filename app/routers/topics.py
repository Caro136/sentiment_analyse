"""监测主题路由（API-03~05，FR-06~FR-09）。

三条写路径刻意分开：改元信息、换词包、换通道关联。词包与通道是「整份配置」，
各自有全量替换的语义；把它们塞进通用的 PUT 会让「没传某个字段」到底是
「不改」还是「清空」变得含糊——那类歧义在配置接口上一定会被误用。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.schemas.monitoring import (
    KeywordGroupStat,
    KeywordItem,
    TopicChannelReplaceRequest,
    TopicCreateRequest,
    TopicDetail,
    TopicKeywordReplaceRequest,
    TopicSummary,
    TopicUpdateRequest,
)
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.monitoring import topics as service
from app.services.monitoring.repository import SqlMonitoringRepository, TopicBundle
from engines.common.db.session import get_db_session
from engines.contracts.enums import KeywordGroup, TopicStatus
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/topics", tags=["topics"])

_WriteTopic = Annotated[UserRecord, Depends(require_permission(Permission.TOPIC_WRITE))]
_ReadTopic = Annotated[UserRecord, Depends(require_permission(Permission.TOPIC_READ))]


async def get_monitoring_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlMonitoringRepository:
    """监测配置仓储依赖。测试用 `app.dependency_overrides` 换成内存替身。"""
    return SqlMonitoringRepository(session)


def _group_stats(bundle: TopicBundle) -> list[KeywordGroupStat]:
    counts: dict[str, int] = {}
    for keyword in bundle.keywords:
        counts[keyword.group_type] = counts.get(keyword.group_type, 0) + 1
    return [
        KeywordGroupStat(group_type=group, label=group.label,
                         count=counts.get(group.value, 0))
        for group in KeywordGroup
    ]


def _summary_fields(bundle: TopicBundle) -> dict:
    """摘要字段。列表与详情共用一份映射，避免两处漏改同一字段。"""
    topic = bundle.topic
    return {
        "topic_id": topic.topic_id,
        "name": topic.name,
        "description": topic.description,
        "source_keyword": topic.source_keyword,
        "owner_id": topic.owner_id,
        "status": topic.status,
        "start_at": topic.start_at,
        "created_at": topic.created_at,
        "keyword_count": len(bundle.keywords),
        "keyword_groups": _group_stats(bundle),
    }


def _to_summary(bundle: TopicBundle) -> TopicSummary:
    return TopicSummary(**_summary_fields(bundle))


def _to_detail(bundle: TopicBundle) -> TopicDetail:
    return TopicDetail(
        **_summary_fields(bundle),
        keywords=[KeywordItem(word=k.word, group_type=k.group_type, weight=k.weight)
                  for k in bundle.keywords],
        channel_ids=list(bundle.channel_ids),
    )


@router.get("", response_model=PageResult[TopicSummary], summary="主题列表")
async def list_topics(
    _actor: _ReadTopic,
    repo=Depends(get_monitoring_repository),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    status: TopicStatus | None = Query(default=None, description="按状态筛选"),
    owner_id: str | None = Query(default=None, max_length=64, description="按负责人筛选"),
    keyword: str | None = Query(default=None, max_length=64, description="按主题名称模糊匹配"),
) -> PageResult[TopicSummary]:
    """主题列表。词包只返回分组统计，详情接口才返回全部词条。"""
    total, bundles = await service.list_topics(
        repo, page=page, page_size=page_size,
        status=status.value if status else None,
        owner_id=owner_id, keyword=keyword)
    return paginate(total, [_to_summary(b) for b in bundles], page, page_size)


@router.post("", response_model=TopicDetail, status_code=201, summary="新建主题")
async def create_topic(payload: TopicCreateRequest, actor: _WriteTopic,
                       repo=Depends(get_monitoring_repository)) -> TopicDetail:
    """新建主题（可一并提交词包与通道关联）。

    `source_keyword` 留空时从词包自动推导——它决定接入层能不能召回到内容，
    而使用者填的是词包，不该被要求了解原始数据的取值。
    """
    bundle = await service.create_topic(
        repo,
        actor=actor,
        name=payload.name,
        description=payload.description,
        source_keyword=payload.source_keyword,
        owner_id=payload.owner_id,
        start_at=payload.start_at,
        status=payload.status.value,
        keywords=payload.keywords,
        channel_ids=payload.channel_ids,
    )
    return _to_detail(bundle)


@router.get("/{topic_id}", response_model=TopicDetail, summary="主题详情")
async def get_topic(topic_id: str, _actor: _ReadTopic,
                    repo=Depends(get_monitoring_repository)) -> TopicDetail:
    return _to_detail(await service.get_topic(repo, topic_id))


@router.put("/{topic_id}", response_model=TopicDetail, summary="更新主题元信息")
async def update_topic(topic_id: str, payload: TopicUpdateRequest, actor: _WriteTopic,
                       repo=Depends(get_monitoring_repository)) -> TopicDetail:
    """更新名称/描述/主题关键词/生效时间/状态（改状态即 FR-06 的启停主题）。

    分析师只能改本人创建的主题（数据范围），管理员不受限。
    """
    bundle = await service.update_topic(
        repo, actor=actor, topic_id=topic_id, name=payload.name,
        description=payload.description, source_keyword=payload.source_keyword,
        start_at=payload.start_at,
        status=payload.status.value if payload.status else None,
    )
    return _to_detail(bundle)


@router.put("/{topic_id}/keywords", response_model=TopicDetail, summary="替换词包")
async def replace_keywords(topic_id: str, payload: TopicKeywordReplaceRequest,
                           actor: _WriteTopic,
                           repo=Depends(get_monitoring_repository)) -> TopicDetail:
    """全量替换词包（品牌词/事件词/竞品词/排除词）。

    同一个词既在召回组又在排除组会被拒绝——那种配置下命中与否取决于匹配顺序。
    """
    bundle = await service.replace_keywords(repo, actor=actor, topic_id=topic_id,
                                            keywords=payload.keywords)
    return _to_detail(bundle)


@router.put("/{topic_id}/channels", response_model=TopicDetail, summary="替换主题启用的通道")
async def replace_channels(topic_id: str, payload: TopicChannelReplaceRequest,
                           actor: _WriteTopic,
                           repo=Depends(get_monitoring_repository)) -> TopicDetail:
    """指定该主题启用哪些信源通道（FR-09）。"""
    bundle = await service.replace_channels(repo, actor=actor, topic_id=topic_id,
                                            channel_ids=payload.channel_ids)
    return _to_detail(bundle)
