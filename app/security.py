"""鉴权依赖：`CurrentUser` 与 `require_permission`（设计规格 8.1）。

分两级，区别在**要不要查库**：

- `require_authenticated`：只验票据签名与时效，不查库。挂在整个 `/api/v1`
  路由组上做「默认拒绝」，任何请求先过这一关。
- `get_current_user`：验签之后回查用户（状态、角色、口令指纹），返回 `UserRecord`。
  需要「是谁」的接口才用它。

这样默认拒绝的成本是一次签名校验，而真正需要身份的接口才付一次主键查询。
FastAPI 会缓存同请求内的依赖，两者同时出现不会查两遍。

**票据来源**优先 Cookie（前端约定，也是 SSE 唯一可行的方式——`EventSource`
不能自定义请求头），其次 `Authorization: Bearer`（便于 curl 与 `/docs` 联调）。
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import forbidden, not_authenticated, session_expired
from app.services.audit.context import bind_actor
from app.services.auth.repository import SqlUserRepository, UserRecord
from app.services.auth.tokens import (
    SessionExpired,
    SessionInvalid,
    SessionPayload,
    read_session,
)
from engines.common.db.session import get_db_session
from engines.contracts.enums import UserRole
from engines.contracts.permissions import Permission, can, in_data_scope

__all__ = [
    "get_user_repository",
    "require_authenticated",
    "get_current_user",
    "CurrentUser",
    "require_permission",
    "require_role",
    "ensure_data_scope",
    "extract_session_token",
]

_BEARER_PREFIX = "bearer "


def extract_session_token(request: Request) -> str:
    """从 Cookie 或 Authorization 头取出票据，取不到返回空串。"""
    token = request.cookies.get(_settings_cookie_name())
    if token:
        return token

    header = request.headers.get("Authorization") or ""
    if header.lower().startswith(_BEARER_PREFIX):
        return header[len(_BEARER_PREFIX):].strip()
    return ""


def _settings_cookie_name() -> str:
    # 延迟导入：模块级导入 settings 会让测试中改 .env 后的覆盖失效
    from engines.contracts.config import get_settings

    return get_settings().session_cookie_name


async def get_user_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlUserRepository:
    """用户仓储依赖。测试通过 `app.dependency_overrides` 换成内存实现。"""
    return SqlUserRepository(session)


async def require_authenticated(
    request: Request,
) -> SessionPayload:
    """默认拒绝的守卫：票据必须存在、签名有效且未过期。

    过期（AUTH_002）与非法（AUTH_001）分开返回：前端对二者的文案不同，
    而「被篡改」这种情况在日志里值得单独看见。
    """
    token = extract_session_token(request)
    if not token:
        raise not_authenticated("请求未携带会话票据")

    try:
        return read_session(token)
    except SessionExpired as exc:
        raise session_expired() from exc
    except SessionInvalid as exc:
        raise not_authenticated(str(exc)) from exc


async def get_current_user(
    payload: Annotated[SessionPayload, Depends(require_authenticated)],
    repo: Annotated[SqlUserRepository, Depends(get_user_repository)],
) -> UserRecord:
    """当前登录用户。会话不可用时抛 AUTH_001（见 service.resolve_session 的说明）。

    解析成功后把操作人写进审计上下文（`app/services/audit/context.py`）：
    这是「谁做的」进入审计记录的唯一入口，此后同一请求内所有被 `@audit`
    装饰的服务调用都会带上它，不必逐层传参。
    """
    from app.services.auth.service import resolve_session

    user = await resolve_session(repo, payload)
    bind_actor(user.user_id)
    return user


# 路由签名里写 `user: CurrentUser` 即可拿到当前用户
CurrentUser = Annotated[UserRecord, Depends(get_current_user)]


def require_permission(*permissions: Permission):
    """权限守卫工厂：要求当前用户**同时**具备列出的全部权限点。

    用「同时具备」而不是「任一具备」：调用点应当写明它真正需要的能力，
    需要任一者可拆成多个路由或直接在函数体内判断——默认宽松会悄悄放大权限。

    权限矩阵在 `engines/contracts/permissions.py`（PRD 第 2 章的唯一映射）。
    """
    async def _guard(user: CurrentUser) -> UserRecord:
        for permission in permissions:
            if not can(user.role, permission):
                raise forbidden(permission.value, detail=f"role={user.role}")
        return user

    return _guard


def require_role(*roles: UserRole):
    """按角色直接守卫。仅用于角色本身即语义的场景（如用户管理）；
    其余一律用 `require_permission`，避免绕过权限矩阵。"""
    async def _guard(user: CurrentUser) -> UserRecord:
        if user.role not in {r.value for r in roles}:
            allowed = "/".join(r.value for r in roles)
            raise forbidden(detail=f"role={user.role}, allowed={allowed}")
        return user

    return _guard


def ensure_data_scope(user: UserRecord, permission: Permission,
                      owner_id: str | None) -> None:
    """数据范围校验（PRD 第 2 章的第三级控制）。

    服务层在拿到资源后调用：`topic:write` 对分析师仅限本人主题，
    矩阵本身不查库，归属由调用方提供。
    """
    if not in_data_scope(user.role, permission, actor_id=user.user_id, owner_id=owner_id):
        raise forbidden(permission.value, detail="仅限本人创建的资源")
