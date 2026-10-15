"""演示账号初始化。

没有用户在库里就没人能登录，而用户管理接口本身又要求管理员身份——
这是典型的引导问题，只能由命令行解决。

用法：
    python -m app.services.auth.seed                 # 建三个演示账号（已存在则跳过）
    python -m app.services.auth.seed --reset         # 已存在的账号：重置口令并恢复默认角色
    python -m app.services.auth.seed --password-env SESSION_SEED_PASSWORD
                                                    # 从环境变量取初始口令，不落命令行历史

默认口令是**演示用**的，脚本每次执行都会把账号与口令打印出来并提示上线前修改。
口令不写进日志文件（走 print 而非 loguru），避免日志归档把它带出去。
"""

import argparse
import asyncio
import os
import sys

from app.services.auth import passwords, service
from app.services.auth.repository import SqlUserRepository
from engines.common.db.session import dispose_engine, session_scope
from engines.contracts.config import get_settings
from engines.contracts.enums import UserRole, UserStatus

# (用户名, 默认口令, 显示名, 角色)
DEMO_ACCOUNTS: list[tuple[str, str, str, UserRole]] = [
    ("admin", "Admin@123", "系统管理员", UserRole.ADMIN),
    ("analyst", "Analyst@123", "舆情分析师", UserRole.ANALYST),
    ("pr", "Pr@12345", "公关运营", UserRole.PR),
]

def _password_for(role: UserRole, default: str, password_env: str | None) -> str:
    """取该账号的初始口令：环境变量优先，其次命令行传入，最后默认值。"""
    if password_env:
        value = os.environ.get(password_env, "")
        if not value:
            raise SystemExit(f"环境变量 {password_env} 为空，未获取到口令")
        return value
    return default


async def run(*, reset: bool, password_env: str | None) -> int:
    """建号并打印结果。

    连接池的释放在**同一个事件循环内**完成（`finally` 里的 `dispose_engine`）：
    池里的连接绑定的是创建它的那个循环，换一个 `asyncio.run()` 去关会报
    「Event loop is closed」——T-02 踩过，这里必须按同一模式收尾。
    """
    created, skipped, reset_count = [], [], []

    try:
        async with session_scope() as session:
            repo = SqlUserRepository(session)

            for username, default_password, display_name, role in DEMO_ACCOUNTS:
                password = _password_for(role, default_password, password_env)
                existing = await repo.get_by_username(username)

                if existing is None:
                    await service.create_user(
                        repo, username=username, password=password,
                        display_name=display_name, role=role.value,
                        status=UserStatus.ACTIVE.value,
                    )
                    created.append((username, password, role))
                    continue

                if reset:
                    await repo.update(existing.user_id, {
                        "password_hash": passwords.hash_password(password),
                        "role": role.value,
                        "status": UserStatus.ACTIVE.value,
                        "display_name": existing.display_name or display_name,
                    })
                    reset_count.append((username, password, role))
                else:
                    skipped.append(username)
    finally:
        await dispose_engine()

    for username, password, role in created:
        print(f"  新建  {username:<10} {role.label:<8} 口令 {password}")
    for username, password, role in reset_count:
        print(f"  重置  {username:<10} {role.label:<8} 口令 {password}")
    for username in skipped:
        print(f"  跳过  {username:<10} 已存在（加 --reset 可重置口令）")

    if created or reset_count:
        print()
        print("以上口令为演示用初始值，上线前请通过用户管理接口逐个修改。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化演示账号")
    parser.add_argument("--reset", action="store_true",
                        help="已存在的账号：重置口令并恢复默认角色与启用状态")
    parser.add_argument("--password-env", default=None, metavar="VAR",
                        help="从指定环境变量读取初始口令（避免口令留在命令行历史）")
    args = parser.parse_args(argv)

    settings = get_settings()
    print(f"目标数据库：{settings.db_name} @ {settings.db_host}:{settings.db_port}")

    try:
        return asyncio.run(run(reset=args.reset, password_env=args.password_env))
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
