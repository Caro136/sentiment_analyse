"""用户管理路由（FR-03，仅管理员）。

路径用 `/api/v1/users` 而不是 `/api/v1/auth/users`：用户是独立资源，
登录相关的 `/auth/*` 只是「会话」这一资源的操作。设计规格 6.1 的接口清单
是示意性的，此处新增 API-31（用户管理）并已在清单中补记。

每个接口都挂 `require_permission(Permission.USER_MANAGE)`——权限矩阵的唯一来源，
不在路由里写 `if user.role == "admin"`。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.schemas.auth import (
    CreateUserRequest,
    ResetPasswordRequest,
    UpdateUserRequest,
    UserDetail,
)
from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.security import get_user_repository, require_permission
from app.services.auth import service
from app.services.auth.repository import UserRecord
from engines.contracts.enums import UserRole, UserStatus
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/users", tags=["users"])

_ManageUser = Annotated[UserRecord, Depends(require_permission(Permission.USER_MANAGE))]


@router.get("", response_model=PageResult[UserDetail], summary="用户列表")
async def list_users(
    _actor: _ManageUser,
    repo=Depends(get_user_repository),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    role: UserRole | None = Query(default=None, description="按角色筛选"),
    status: UserStatus | None = Query(default=None, description="按状态筛选"),
    keyword: str | None = Query(default=None, max_length=64, description="用户名或显示名模糊匹配"),
) -> PageResult[UserDetail]:
    total, records = await service.list_users(
        repo, page=page, page_size=page_size,
        role=role.value if role else None,
        status=status.value if status else None,
        keyword=keyword,
    )
    return paginate(total, [UserDetail.from_record(r) for r in records], page, page_size)


@router.post("", response_model=UserDetail, status_code=201, summary="新增用户")
async def create_user(payload: CreateUserRequest, _actor: _ManageUser,
                      repo=Depends(get_user_repository)) -> UserDetail:
    record = await service.create_user(
        repo,
        username=payload.username,
        password=payload.password,
        display_name=payload.display_name,
        role=payload.role.value,
        status=payload.status.value,
    )
    return UserDetail.from_record(record)


@router.put("/{user_id}", response_model=UserDetail, summary="修改用户角色或状态")
async def update_user(user_id: str, payload: UpdateUserRequest, actor: _ManageUser,
                      repo=Depends(get_user_repository)) -> UserDetail:
    record = await service.update_user(
        repo,
        actor_id=actor.user_id,
        user_id=user_id,
        display_name=payload.display_name,
        role=payload.role.value if payload.role else None,
        status=payload.status.value if payload.status else None,
    )
    return UserDetail.from_record(record)


@router.post("/{user_id}/reset-password", response_model=UserDetail, summary="重置口令")
async def reset_password(user_id: str, payload: ResetPasswordRequest, _actor: _ManageUser,
                         repo=Depends(get_user_repository)) -> UserDetail:
    """重置口令后，该用户的**既有会话立即失效**（票据里的口令指纹对不上）。"""
    record = await service.reset_password(repo, user_id=user_id,
                                          new_password=payload.new_password)
    return UserDetail.from_record(record)
