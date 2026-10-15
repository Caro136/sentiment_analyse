"""用户仓储：`sys_user` 的唯一读写入口。

**为什么要多一层**：服务层「不直接写 SQL」是设计规格 3.2 的职责边界，
而且把 SQL 收在一处之后，测试可以在不连数据库的前提下替换成内存实现
（`tests/fakes.py::InMemoryUserRepository`），鉴权逻辑才可能离线跑。

对外暴露的是 `UserRecord` 数据类而非 ORM 对象：ORM 实例带会话绑定，
出了 `session_scope()` 就不可用，接口层拿到很危险；转成普通对象后
生命周期与用法都明确。
"""

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select

from engines.common.db.models import SysUser

__all__ = ["UserRecord", "UserRepository", "SqlUserRepository", "new_user_id"]


@dataclass(frozen=True)
class UserRecord:
    """用户的可传输快照（不含口令哈希以外的敏感信息）。"""

    user_id: str
    username: str
    password_hash: str
    display_name: str
    role: str
    status: str
    created_at: int
    last_login_at: int | None = None

    def to_dict(self) -> dict:
        """对外序列化用（**不含口令哈希**）。"""
        return {
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name or self.username,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


def new_user_id() -> str:
    """生成用户业务ID。

    与 `content_uid`、`topic_id` 等既有标识一样用「前缀 + 时间 + 随机」，
    便于日志里一眼看出是什么对象；不用自增主键是为了避免把内部行号暴露到接口。
    """
    import secrets
    import time

    return f"u{int(time.time())}{secrets.token_hex(3)}"


class UserRepository(Protocol):
    """用户读写接口。实现类须满足的契约，供内存替身与 SQL 实现共用。"""

    async def get_by_username(self, username: str) -> UserRecord | None: ...

    async def get_by_user_id(self, user_id: str) -> UserRecord | None: ...

    async def list_users(self, *, page: int, page_size: int, role: str | None = None,
                         status: str | None = None, keyword: str | None = None,
                         ) -> tuple[int, list[UserRecord]]: ...

    async def count_active_admins(self) -> int: ...

    async def create(self, record: UserRecord) -> UserRecord: ...

    async def update(self, user_id: str, fields: dict) -> UserRecord | None: ...


def _to_record(row: SysUser) -> UserRecord:
    return UserRecord(
        user_id=row.user_id,
        username=row.username,
        password_hash=row.password_hash,
        display_name=row.display_name or row.username,
        role=row.role,
        status=row.status,
        created_at=row.created_at,
        last_login_at=row.last_login_at,
    )


class SqlUserRepository:
    """基于 SQLAlchemy 异步会话的实现。

    会话由调用方（依赖注入）提供，仓储自己不管理事务边界——
    事务由 `session_scope()` 统一提交/回滚，仓储中途自行提交会把
    「一个用例一次事务」的语义打散。
    """

    def __init__(self, session) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> UserRecord | None:
        result = await self._session.execute(
            select(SysUser).where(SysUser.username == username)
        )
        row = result.scalar_one_or_none()
        return _to_record(row) if row else None

    async def get_by_user_id(self, user_id: str) -> UserRecord | None:
        result = await self._session.execute(
            select(SysUser).where(SysUser.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        return _to_record(row) if row else None

    async def list_users(self, *, page: int, page_size: int, role: str | None = None,
                         status: str | None = None, keyword: str | None = None,
                         ) -> tuple[int, list[UserRecord]]:
        conditions = []
        if role:
            conditions.append(SysUser.role == role)
        if status:
            conditions.append(SysUser.status == status)
        if keyword:
            like = f"%{keyword}%"
            conditions.append(SysUser.username.like(like) | SysUser.display_name.like(like))

        total_stmt = select(func.count()).select_from(SysUser)
        rows_stmt = select(SysUser).order_by(SysUser.created_at.desc())
        for condition in conditions:
            total_stmt = total_stmt.where(condition)
            rows_stmt = rows_stmt.where(condition)

        total = int((await self._session.execute(total_stmt)).scalar() or 0)
        rows_stmt = rows_stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await self._session.execute(rows_stmt)).scalars().all()
        return total, [_to_record(r) for r in rows]

    async def count_active_admins(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(SysUser)
            .where(SysUser.role == "admin", SysUser.status == "active")
        )
        return int(result.scalar() or 0)

    async def create(self, record: UserRecord) -> UserRecord:
        row = SysUser(
            user_id=record.user_id,
            username=record.username,
            password_hash=record.password_hash,
            display_name=record.display_name,
            role=record.role,
            status=record.status,
            created_at=record.created_at,
            last_login_at=record.last_login_at,
        )
        self._session.add(row)
        await self._session.flush()   # 立刻拿约束冲突（用户名唯一），而不是等到提交
        return _to_record(row)

    async def update(self, user_id: str, fields: dict) -> UserRecord | None:
        row = (await self._session.execute(
            select(SysUser).where(SysUser.user_id == user_id)
        )).scalar_one_or_none()
        if row is None:
            return None
        for key, value in fields.items():
            setattr(row, key, value)
        await self._session.flush()
        return _to_record(row)
