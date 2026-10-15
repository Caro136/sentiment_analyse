"""认证路由（API-01/02，FR-01）。

**两个 router 是有意分开的**：`public_router` 只放登录——它是全站唯一
不需要会话的接口；`router` 放其余需要会话的接口，由 `app/main.py` 挂进
带默认拒绝守卫的 `api_router`。

为什么不用「一个 router + 给登录路由单独免检」：FastAPI 的 router 级依赖
无法对单条路由豁免，只能反过来给每条路由挂依赖，那样一旦新增路由忘了挂，
结果就是**默认可访问**——这是鉴权最容易出的漏子。分成两个 router 之后，
放进 `public_router` 的动作本身就是显式的「这里公开」。
"""

from fastapi import APIRouter, Depends, Request, Response

from app.observability import client_ip_from_scope
from app.schemas.auth import LoginRequest, UserInfo
from app.security import CurrentUser, get_user_repository
from app.services.auth import service
from app.services.auth.tokens import clear_session_cookie, set_session_cookie

public_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@public_router.post("/login", response_model=UserInfo, summary="登录")
async def login(payload: LoginRequest, request: Request, response: Response,
                repo=Depends(get_user_repository)) -> UserInfo:
    """校验凭据并下发会话 Cookie。

    会话票据只放进 `HttpOnly` Cookie，**不放进响应体**：放进响应体就等于
    给了前端一份可被脚本读取的凭据，而前端并不需要它（浏览器会自动携带）。
    """
    user, token = await service.login(repo, payload.username, payload.password,
                                      ip=client_ip_from_scope(request.scope))
    set_session_cookie(response, token)
    return UserInfo.from_record(user)


@router.post("/logout", status_code=204, summary="登出")
async def logout(response: Response) -> None:
    """清除会话 Cookie。

    无服务端会话表，因此登出是「客户端丢弃票据」；如需强制下线其他设备，
    走管理员重置口令（会改变口令指纹，使既有票据全部失效）。

    ⚠️ 这里**必须返回 None**，不能 `return Response(...)`：FastAPI 只在端点
    未返回 Response 对象时才把注入的 `response` 头合并到最终响应上
    （routing.py 的 `response.headers.raw.extend(...)`）。自己构造 Response
    会把 `Set-Cookie` 整个丢掉——表现为「点了登出，刷新后还在线」。
    """
    clear_session_cookie(response)


@router.get("/me", response_model=UserInfo, summary="当前用户")
async def current_user(user: CurrentUser) -> UserInfo:
    """返回当前会话对应的用户，供前端刷新页面后恢复登录态。"""
    return UserInfo.from_record(user)


__all__ = ["public_router", "router"]
