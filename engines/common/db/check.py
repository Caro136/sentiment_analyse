"""DDL 与 ORM 一致性校验。

**DDL 是权威定义**，ORM 是它的代码映射。两者漂移是本项目最容易埋雷的地方——
表加了字段但模型没加，运行到那一步才报错，且往往在演示时才发现。

本工具解析 `sql/` 下的建表语句，与 `Base.metadata` 逐表比对列名，不一致即报错。

用法：
    python -m engines.common.db.check          # 只校验
    python -m engines.common.db.check --create # 校验通过后在目标库建表（不删已有表）
"""

import argparse
import os
import re
import sys

from engines.common.db.base import Base
from engines.common.db.models import *  # noqa: F401,F403  触发模型注册
from engines.contracts.config import PROJECT_ROOT

SQL_DIRS = [PROJECT_ROOT / "sql" / "analysis", PROJECT_ROOT / "sql" / "business"]

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+`(?P<name>\w+)`\s*\((?P<body>.*?)\n\)\s*ENGINE",
    re.IGNORECASE | re.DOTALL,
)
_COLUMN_RE = re.compile(r"^\s*`(?P<name>\w+)`\s+(?P<type>[a-zA-Z]+)", re.MULTILINE)


def parse_ddl() -> dict[str, list[str]]:
    """解析 sql/ 下的建表语句，返回 {表名: [列名...]}。"""
    tables: dict[str, list[str]] = {}
    for directory in SQL_DIRS:
        if not directory.is_dir():
            continue
        for filename in sorted(os.listdir(directory)):
            if not filename.endswith(".sql"):
                continue
            text = (directory / filename).read_text(encoding="utf-8")
            for match in _CREATE_TABLE_RE.finditer(text):
                name = match.group("name")
                columns = [m.group("name") for m in _COLUMN_RE.finditer(match.group("body"))]
                tables[name] = columns
    return tables


def compare() -> list[str]:
    """比对 DDL 与 ORM，返回问题列表（空表示一致）。"""
    ddl_tables = parse_ddl()
    orm_tables = {name: [c.name for c in table.columns]
                  for name, table in Base.metadata.tables.items()}

    problems: list[str] = []

    for name in sorted(set(ddl_tables) - set(orm_tables)):
        problems.append(f"DDL 中存在但 ORM 未定义的表：{name}")
    for name in sorted(set(orm_tables) - set(ddl_tables)):
        problems.append(f"ORM 中定义但 DDL 中缺失的表：{name}")

    for name in sorted(set(ddl_tables) & set(orm_tables)):
        ddl_cols, orm_cols = ddl_tables[name], orm_tables[name]
        for col in ddl_cols:
            if col not in orm_cols:
                problems.append(f"{name}.{col}：DDL 有该列，ORM 缺失")
        for col in orm_cols:
            if col not in ddl_cols:
                problems.append(f"{name}.{col}：ORM 有该列，DDL 缺失")
        if ddl_cols != orm_cols:
            only_ddl_order = [c for c in ddl_cols if c in orm_cols]
            if only_ddl_order != [c for c in orm_cols if c in ddl_cols]:
                problems.append(f"{name}：列顺序不一致（不影响功能，但建议对齐便于阅读）")

    return problems


async def create_tables(verbose: bool = True) -> None:
    """在目标库建表（CREATE TABLE IF NOT EXISTS，不触碰已有表）。"""
    from engines.common.db.session import dispose_engine, get_engine
    from engines.contracts.config import get_settings

    engine = get_engine()
    settings = get_settings()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    if verbose:
        print(f"已在 {settings.db_name} 中创建 {len(Base.metadata.tables)} 张表（已存在的跳过）")
    await dispose_engine()


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="DDL 与 ORM 一致性校验")
    parser.add_argument("--create", action="store_true", help="校验通过后在目标库建表")
    args = parser.parse_args(argv)

    ddl_tables = parse_ddl()
    print(f"解析到 DDL 表 {len(ddl_tables)} 张，ORM 表 {len(Base.metadata.tables)} 张")

    problems = compare()
    if problems:
        print("\n发现不一致：")
        for p in problems:
            print(f"  - {p}")
        return 1

    print("DDL 与 ORM 完全一致 ✓")

    if args.create:
        import asyncio
        asyncio.run(create_tables())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
