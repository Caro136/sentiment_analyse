"""舆情内容路由（API-08/09，FR-42~FR-44）。

两个接口都是只读，权限点 `content:read`（三类角色共有——工作台是分析师与公关的
日常视图，管理员也要能查看以排障）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.schemas.content import ContentDetail, ContentItem
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.content import service
from app.services.content.repository import SqlContentRepository
from engines.common.db.session import get_db_session
from engines.contracts.enums import RiskLevel, Sentiment, SourceType
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

_ReadContent = Annotated[UserRecord, Depends(require_permission(Permission.CONTENT_READ))]


async def get_content_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlContentRepository:
    """内容查询仓储依赖。测试用 `app.dependency_overrides` 换成内存替身。"""
    return SqlContentRepository(session)


@router.get("", response_model=PageResult[ContentItem], summary="舆情动态流")
async def list_contents(
    _actor: _ReadContent,
    repo=Depends(get_content_repository),
    session: AsyncSession = Depends(get_db_session),
    topic_id: str | None = Query(default=None, max_length=64, description="监测主题"),
    source_type: SourceType | None = Query(default=None, description="信源类型"),
    sentiment: Sentiment | None = Query(default=None, description="情感倾向"),
    risk_level: RiskLevel | None = Query(default=None, description="风险等级"),
    start_time: int | None = Query(default=None, description="发布时间起（Unix 时间戳）"),
    end_time: int | None = Query(default=None, description="发布时间止（Unix 时间戳）"),
    keyword: str | None = Query(default=None, max_length=64, description="正文关键词"),
    is_suspected_bot: bool | None = Query(default=None, description="疑似水军"),
    is_manual_override: bool | None = Query(default=None, description="人工复核过"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[ContentItem]:
    """多维筛选 + 分页，按**发布时间倒序**。

    筛选分两层：时间窗与关键词落在原始表（正文与发布时间只在那里），
    主题/情感/风险/水军/人工复核落在分析标签表（见 `services/content/service.py`）。
    列表正文截断 200 字并标记 `text_truncated`，全文走详情接口。
    """
    total, items = await service.list_contents(
        repo, session, page=page, page_size=page_size, topic_id=topic_id,
        source_type=source_type.value if source_type else None,
        sentiment=sentiment.value if sentiment else None,
        risk_level=risk_level.value if risk_level else None,
        start_time=start_time, end_time=end_time, keyword=keyword,
        is_suspected_bot=is_suspected_bot, is_manual_override=is_manual_override)

    return paginate(total, [ContentItem(**item) for item in items], page, page_size)


@router.get("/{content_uid}", response_model=ContentDetail, summary="内容详情")
async def get_content(content_uid: str, _actor: _ReadContent,
                      repo=Depends(get_content_repository),
                      session: AsyncSession = Depends(get_db_session)) -> ContentDetail:
    """原文全文 + OCR/ASR 证据 + 判定依据（FR-44）。

    `content_uid` 形如 `{source_type}:{source_table}:{business_id}`（含冒号，
    前端需 `encodeURIComponent`）。多模态解析失败时 `modality_assets` 里会带上
    `parse_status=failed` 与失败原因——解析失败不阻断入库，详情页要能说明这一点。
    """
    detail = await service.get_content_detail(repo, session, content_uid)
    return ContentDetail(**detail)
