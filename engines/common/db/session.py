"""数据库连接与会话管理。

要点：

1. **引擎与会话工厂是进程内单例**。反复创建 engine 会耗尽连接池，
   配置热更新（PRD FR-53）时通过 `reset_engine()` 显式重建。
2. **`pool_pre_ping=True`**：MySQL 会主动断开空闲连接（默认 8 小时），
   没有预检就会在长时间无请求后抛 "MySQL server has gone away"。
3. **`session_scope()` 是唯一推荐的事务入口**：正常提交、异常回滚，
   避免各处手写 try/except 导致连接泄漏。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from engines.contracts.config import Settings, get_settings

# 连接池参数：演示规模下单进程足够；瓶颈在 LLM 调用而非数据库
POOL_SIZE = 10
MAX_OVERFLOW = 20
POOL_RECYCLE_SECONDS = 3600

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    """获取异步引擎单例。"""
    global _engine
    if _engine is None:
        cfg = settings or get_settings()
        _engine = create_async_engine(
            cfg.database_url,
            echo=False,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=POOL_RECYCLE_SECONDS,
        )
    return _engine


def get_session_factory(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    """获取会话工厂单例。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(settings),
            class_=AsyncSession,
            expire_on_commit=False,   # 提交后仍可读取属性，避免接口序列化时触发额外查询
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """事务性会话上下文：正常提交，异常回滚。

    用法：
        async with session_scope() as session:
            session.add(row)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖注入用的会话提供者。

    用法：
        @router.get("/x")
        async def handler(session: Annotated[AsyncSession, Depends(get_db_session)]): ...
    """
    async with session_scope() as session:
        yield session


async def dispose_engine() -> None:
    """释放连接池（服务关闭时调用）。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


def reset_engine() -> None:
    """同步地丢弃引擎引用（配置变更后调用，下次取用时自动重建）。"""
    global _engine, _session_factory
    _engine = None
    _session_factory = None


async def ping() -> tuple[bool, str]:
    """探测数据库连通性，供 /readyz 使用。返回 (是否可达, 说明)。"""
    try:
        from sqlalchemy import text

        async with session_scope() as session:
            result = await session.execute(text("SELECT VERSION()"))
            version = result.scalar()
        return True, f"MySQL {version}"
    except Exception as exc:  # noqa: BLE001
        from engines.common.logging import mask_secret

        return False, mask_secret(f"{type(exc).__name__}: {exc}")[:200]
