"""认证与用户管理的用例层（FR-01~FR-03）。

职责边界：只做用例判断（凭据校验、账号状态、管理动作的约束），
不碰 HTTP（Cookie 的写与清在路由层）、不写 SQL（在 `repository`）。

两个容易漏掉的安全细节，都在这里落地：

1. **账号枚举的时序侧信道**：用户不存在时也对固定哈希跑一次等价开销的校验，
   否则「响应快 = 该用户不存在」可以被用来探测账号。
2. **不区分用户名错与口令错**：两者都回 AUTH_004 且文案相同。

登录与用户管理的留痕（FR-04）用 T-20 的 `@audit` 装饰器声明，
不由本模块自己写 `audit_log`——覆盖范围与动作名统一在装饰器那一处维护。
被审计的函数里用 `note_change()` 登记前后值（业务判断用的那份，不重查数据库）。
"""

import time

from loguru import logger

from app.errors import (
    account_disabled,
    bad_credentials,
    conflict,
    duplicate,
    invalid_request,
    not_authenticated,
    not_found,
)
from app.services.audit import audit, note_change
from app.services.audit.context import bind_actor
from app.services.auth import passwords
from app.services.auth.repository import UserRecord, new_user_id
from app.services.auth.tokens import SessionPayload, issue_session
from engines.contracts.enums import AuditAction, UserRole, UserStatus

__all__ = [
    "authenticate",
    "login",
    "resolve_session",
    "list_users",
    "create_user",
    "update_user",
    "reset_password",
]


def _normalize_username(username: str) -> str:
    """登录名大小写不敏感（统一小写存储与比对），首尾空白不计。"""
    return (username or "").strip().lower()


def _check_password(password: str, username: str = "") -> None:
    problems = passwords.password_problems(password, username=username)
    if problems:
        raise invalid_request("口令强度不足：" + "，".join(problems))


def _check_role(role: str) -> str:
    try:
        return UserRole(role).value
    except ValueError:
        allowed = "/".join(r.value for r in UserRole)
        raise invalid_request(f"角色取值不合法，可选：{allowed}") from None


# ---------------------------------------------------------------------------
# 登录与会话
# ---------------------------------------------------------------------------

async def authenticate(repo, username: str, password: str, *,
                       ip: str | None = None) -> UserRecord:
    """校验凭据，返回用户。任何失败都以 AUTH_004/AUTH_005 抛出。"""
    name = _normalize_username(username)
    user = await repo.get_by_username(name)

    if user is None:
        # 走一次等价开销的校验，抹掉「用户不存在」的耗时差异
        passwords.verify_password(password, passwords.DUMMY_HASH)
        logger.warning("登录失败（用户不存在） username={} ip={}", name, ip or "-")
        raise bad_credentials()

    if not passwords.verify_password(password, user.password_hash):
        logger.warning("登录失败（口令错误） username={} ip={}", name, ip or "-")
        raise bad_credentials()

    if user.status != UserStatus.ACTIVE.value:
        logger.warning("登录被拒（账号停用） username={} ip={}", name, ip or "-")
        raise account_disabled()

    # 哈希参数已升级时静默换代：老用户的弱参数哈希在本次登录后变成强参数
    if passwords.needs_rehash(user.password_hash):
        user = await repo.update(user.user_id,
                                 {"password_hash": passwords.hash_password(password)}) or user

    return user


@audit(AuditAction.LOGIN, target="username:{username}")
async def login(repo, username: str, password: str, *,
                ip: str | None = None) -> tuple[UserRecord, str]:
    """登录用例：校验凭据 → 记录登录时间 → 签发会话票据。

    被 `@audit` 覆盖：**失败也留痕**（口令错误、账号停用、用户不存在），
    这正是审计最该记的场景。成功后补登操作人——登录这一步鉴权依赖还没机会
    写上下文（此刻还没有会话），审计行里的 user_id 就来自这里。
    """
    user = await authenticate(repo, username, password, ip=ip)

    now = int(time.time())
    updated = await repo.update(user.user_id, {"last_login_at": now})
    if updated:
        user = updated

    token = issue_session(user.user_id, user.password_hash)
    bind_actor(user.user_id)
    note_change(after={"user_id": user.user_id, "role": user.role})
    logger.info("登录成功 username={} role={} ip={}", user.username, user.role, ip or "-")
    return user, token


async def resolve_session(repo, payload: SessionPayload) -> UserRecord:
    """按票据载荷取回当前用户，并校验会话仍然可用。

    每请求回查数据库是刻意的：角色调整、账号停用、口令重置都要**立即生效**，
    而不是等票据过期（票据默认 8 小时，那期间旧权限一直有效）。
    代价是一次主键查询，对演示规模可以忽略。

    账号已停用 / 用户已删除 / 口令指纹不匹配（改过口令）都回 AUTH_001「未登录」，
    而不是 AUTH_005「账号停用」——从客户端视角这些会话都该被丢弃并跳登录页；
    「账号停用」这句提示留给登录动作去说，那里用户才有可操作的空间。
    """
    user = await repo.get_by_user_id(payload.user_id)
    if user is None:
        raise not_authenticated("会话对应的用户不存在")

    if user.status != UserStatus.ACTIVE.value:
        raise not_authenticated("账号已停用，会话失效")

    if payload.fingerprint != passwords.password_fingerprint(user.password_hash):
        raise not_authenticated("口令已变更，会话失效")

    return user


# ---------------------------------------------------------------------------
# 用户管理（FR-03，仅管理员）
# ---------------------------------------------------------------------------

async def list_users(repo, *, page: int = 1, page_size: int = 20,
                     role: str | None = None, status: str | None = None,
                     keyword: str | None = None) -> tuple[int, list[UserRecord]]:
    """分页查询用户。筛选值非法时直接报错，而不是静默返回空列表。"""
    if role:
        role = _check_role(role)
    if status:
        try:
            status = UserStatus(status).value
        except ValueError:
            raise invalid_request("状态取值不合法，可选：active/disabled") from None
    return await repo.list_users(page=page, page_size=page_size, role=role,
                                 status=status, keyword=keyword)


# target 用用户名而非 user_id：user_id 是函数内部生成的，装饰器看不到；
# 其余用户类动作（改角色、重置口令）都能拿到 user_id，统一为 user:<标识>
@audit(AuditAction.USER_CREATE, target="user:{username}")
async def create_user(repo, *, username: str, password: str, role: str,
                      display_name: str = "", status: str = UserStatus.ACTIVE.value,
                      ) -> UserRecord:
    """新增用户。用户名唯一（大小写不敏感）。"""
    name = _normalize_username(username)
    if not name:
        raise invalid_request("用户名不能为空")
    if len(name) > 64:
        raise invalid_request("用户名不超过 64 位")

    _check_password(password, name)
    role_value = _check_role(role)

    try:
        status_value = UserStatus(status).value
    except ValueError:
        raise invalid_request("状态取值不合法，可选：active/disabled") from None

    if await repo.get_by_username(name) is not None:
        raise duplicate(f"用户名 {name} 已存在", detail=f"username={name}")

    record = UserRecord(
        user_id=new_user_id(),
        username=name,
        password_hash=passwords.hash_password(password),
        display_name=(display_name or name).strip()[:128],
        role=role_value,
        status=status_value,
        created_at=int(time.time()),
        last_login_at=None,
    )
    created = await repo.create(record)
    # 只记用户公开字段（to_dict 不含口令哈希）；新增没有前值
    note_change(after=created.to_dict())
    logger.info("新增用户 username={} role={} status={}", created.username,
                created.role, created.status)
    return created


@audit(AuditAction.USER_UPDATE, target="user:{user_id}")
async def update_user(repo, *, actor_id: str, user_id: str, display_name: str | None = None,
                      role: str | None = None, status: str | None = None) -> UserRecord:
    """修改显示名 / 角色 / 状态。

    两条业务规则拦在这里（都属于「不报错但会把系统锁死」的那类）：
      1. 管理员不能改自己的角色或状态——手一滑就把自己锁在门外，且无人能救；
      2. 系统必须留至少一个启用的管理员——否则用户管理入口彻底失守。
    其余情况（如把最后一个管理员降级）本来就是 1 的特例，一并由 2 兜住。
    """
    target = await repo.get_by_user_id(user_id)
    if target is None:
        raise not_found(f"用户不存在：{user_id}", detail=f"user_id={user_id}")

    fields: dict = {}

    if display_name is not None:
        fields["display_name"] = display_name.strip()[:128] or target.username

    if role is not None and _check_role(role) != target.role:
        fields["role"] = _check_role(role)

    if status is not None:
        try:
            status_value = UserStatus(status).value
        except ValueError:
            raise invalid_request("状态取值不合法，可选：active/disabled") from None
        if status_value != target.status:
            fields["status"] = status_value

    if not fields:
        return target

    # 顺序有意为之：先判「系统还剩几个管理员」，再判「是不是改自己」。
    # 只有一个管理员而他停用自己时，前者给出的提示（必须保留至少一个管理员）
    # 比后者（不能改自己的状态）更准确地说明了问题所在。
    demoting_admin = (
        target.role == UserRole.ADMIN.value
        and target.status == UserStatus.ACTIVE.value
        and (fields.get("role", target.role) != UserRole.ADMIN.value
             or fields.get("status", target.status) != UserStatus.ACTIVE.value)
    )
    if demoting_admin and await repo.count_active_admins() <= 1:
        raise conflict("系统必须保留至少一个启用状态的管理员")

    if user_id == actor_id and ({"role", "status"} & fields.keys()):
        raise conflict("不能修改当前登录账号的角色或状态",
                       detail="请由其他管理员操作")

    updated = await repo.update(user_id, fields)
    if updated is None:
        raise not_found(f"用户不存在：{user_id}")
    # 前后值都记：FR-04 的验收要求「可查到操作人、时间、变更前后值」
    note_change(before=target.to_dict(), after=updated.to_dict())
    logger.info("修改用户 user_id={} 变更字段={}", user_id, sorted(fields))
    return updated


@audit(AuditAction.USER_RESET_PASSWORD, target="user:{user_id}")
async def reset_password(repo, *, user_id: str, new_password: str) -> UserRecord:
    """重置口令。旧会话因口令指纹变化而立即失效（见 tokens 模块说明）。

    审计只记「某人的口令被重置」，**不记任何哈希**——新旧哈希都是敏感值，
    落进审计表等于给了一份可离线爆破的材料，而审计的用途并不需要它。
    """
    target = await repo.get_by_user_id(user_id)
    if target is None:
        raise not_found(f"用户不存在：{user_id}", detail=f"user_id={user_id}")

    _check_password(new_password, target.username)

    updated = await repo.update(user_id, {"password_hash": passwords.hash_password(new_password)})
    if updated is None:
        raise not_found(f"用户不存在：{user_id}")
    note_change(before={"username": target.username, "password_reset": False},
                after={"username": updated.username, "password_reset": True})
    logger.warning("重置口令 username={}（该用户既有会话已失效）", updated.username)
    return updated
