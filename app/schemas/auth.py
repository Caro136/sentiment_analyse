"""认证与用户管理的请求响应模型。

`UserInfo` 的四个字段就是前端 `api/types.ts::UserInfo` 的约定，**不要改名**；
需要暴露更多信息时另开模型（如 `UserDetail`），不要往 `UserInfo` 上加字段——
它同时是登录、`/auth/me`、以及前端会话缓存的载荷。

口令字段一律 `repr=False`：Pydantic 的 `model_validate` 出错时会把入参打进
异常消息，而异常消息可能进日志（`_SECRET_PATTERNS` 未必覆盖得到）。
"""

from pydantic import BaseModel, ConfigDict, Field

from app.services.auth.repository import UserRecord
from engines.contracts.enums import UserRole, UserStatus

__all__ = [
    "LoginRequest",
    "UserInfo",
    "UserDetail",
    "CreateUserRequest",
    "UpdateUserRequest",
    "ResetPasswordRequest",
]

MIN_USERNAME_LENGTH = 2
MAX_USERNAME_LENGTH = 64
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class LoginRequest(BaseModel):
    """登录入参（FR-01）。"""

    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH,
                          description="登录名")
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH,
                          repr=False, description="口令")


class UserInfo(BaseModel):
    """当前用户信息。与前端 `UserInfo` 严格对齐。"""

    user_id: str
    username: str
    display_name: str
    role: UserRole

    @classmethod
    def from_record(cls, record: UserRecord) -> "UserInfo":
        return cls(
            user_id=record.user_id,
            username=record.username,
            display_name=record.display_name or record.username,
            role=UserRole(record.role),
        )


class UserDetail(UserInfo):
    """用户管理视角的完整信息（FR-03）。**永远不含口令哈希。**"""

    model_config = ConfigDict(from_attributes=True)

    status: UserStatus
    created_at: int
    last_login_at: int | None = None

    @classmethod
    def from_record(cls, record: UserRecord) -> "UserDetail":
        return cls(
            user_id=record.user_id,
            username=record.username,
            display_name=record.display_name or record.username,
            role=UserRole(record.role),
            status=UserStatus(record.status),
            created_at=record.created_at,
            last_login_at=record.last_login_at,
        )


class CreateUserRequest(BaseModel):
    """新增用户（FR-03，仅管理员）。"""

    username: str = Field(min_length=MIN_USERNAME_LENGTH, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH,
                          repr=False, description="初始口令，需含字母与数字")
    display_name: str = Field(default="", max_length=128)
    role: UserRole = Field(default=UserRole.ANALYST)
    status: UserStatus = Field(default=UserStatus.ACTIVE)


class UpdateUserRequest(BaseModel):
    """修改用户：字段可省略，省略即不改。

    角色与状态**不在**这里做「不能改自己」的校验——那属于业务规则，
    放在服务层（service.update_user）以便离线单测，且错误码统一为 BIZ_203。
    """

    display_name: str | None = Field(default=None, max_length=128)
    role: UserRole | None = None
    status: UserStatus | None = None


class ResetPasswordRequest(BaseModel):
    """重置口令（FR-03）。重置后该用户的既有会话立即失效。"""

    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH,
                              repr=False)
