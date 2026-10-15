"""数据库初始化：按顺序执行建库建表脚本。

三个脚本的分工：
  1. `data/sql/00_schema.sql`           原始区 9 张表（项目只读，由数据生成器产出）
  2. `sql/analysis/01_analysis_schema.sql`  分析区 9 张表
  3. `sql/business/01_business_schema.sql`  业务区 13 张表

⚠️ 三个脚本均含 `DROP TABLE IF EXISTS`——**执行会清空同名的已有表**。
   本工具默认先做"库是否已存在"检查，已存在时要求显式 `--force` 才继续。

用法：
    python -m engines.common.db.init_db              # 建库建表（库已存在则中止）
    python -m engines.common.db.init_db --force      # 库已存在时也执行（会重建表）
    python -m engines.common.db.init_db --with-data  # 额外导入模拟数据集
    python -m engines.common.db.init_db --status     # 只看各表当前行数
"""

import argparse
import os
import sys

import pymysql
from pymysql.constants import CLIENT

from engines.contracts.config import PROJECT_ROOT, get_settings

SCHEMA_FILES = [
    PROJECT_ROOT / "data" / "sql" / "00_schema.sql",
    PROJECT_ROOT / "sql" / "analysis" / "01_analysis_schema.sql",
    PROJECT_ROOT / "sql" / "business" / "01_business_schema.sql",
]

DATA_FILES = [
    PROJECT_ROOT / "data" / "sql" / "50_platform_account.sql",
    PROJECT_ROOT / "data" / "sql" / "10_social_media.sql",
    PROJECT_ROOT / "data" / "sql" / "20_short_video.sql",
    PROJECT_ROOT / "data" / "sql" / "30_news_portal.sql",
    PROJECT_ROOT / "data" / "sql" / "40_industry_forum.sql",
]

# 建表脚本中会创建的表（用于执行后核对）
EXPECTED_TABLES = 31


def connect(settings, database: str | None = None) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=database,
        charset=settings.db_charset,
        autocommit=True,
        client_flag=CLIENT.MULTI_STATEMENTS,
    )


def database_exists(settings) -> bool:
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES LIKE %s", (settings.db_name,))
            return cur.fetchone() is not None


def execute_file(conn, path) -> None:
    """执行一个 SQL 文件（支持多语句）。"""
    text = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(text)
        while cur.nextset():
            pass


def list_tables(conn) -> list[tuple[str, int]]:
    """返回 [(表名, 行数)]。"""
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES")
        tables = [row[0] for row in cur.fetchall()]
        result = []
        for name in sorted(tables):
            cur.execute(f"SELECT COUNT(*) FROM `{name}`")
            result.append((name, cur.fetchone()[0]))
    return result


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="数据库初始化")
    parser.add_argument("--force", action="store_true",
                        help="目标库已存在时仍然执行（会 DROP 并重建同名表）")
    parser.add_argument("--with-data", action="store_true", help="额外导入模拟数据集")
    parser.add_argument("--status", action="store_true", help="只查看各表行数，不做变更")
    args = parser.parse_args(argv)

    settings = get_settings()
    print(f"目标数据库：{settings.db_name} @ {settings.db_host}:{settings.db_port}")

    try:
        exists = database_exists(settings)
    except Exception as exc:  # noqa: BLE001
        from engines.common.logging import mask_secret
        print(f"连接失败：{mask_secret(str(exc))[:200]}")
        return 1

    if args.status:
        if not exists:
            print("数据库尚未创建")
            return 1
        with connect(settings, settings.db_name) as conn:
            rows = list_tables(conn)
        print(f"共 {len(rows)} 张表：")
        for name, count in rows:
            print(f"  {name:<28} {count:>8} 行")
        return 0

    if exists and not args.force:
        print("数据库已存在。加 --force 可重建表（会清空同名的已有表），"
              "或加 --status 查看现状。")
        return 1

    with connect(settings) as conn:
        for path in SCHEMA_FILES:
            if not path.exists():
                print(f"缺少脚本：{path}")
                return 1
            print(f"执行 {path.relative_to(PROJECT_ROOT)} ...")
            execute_file(conn, path)

        if args.with_data:
            with connect(settings, settings.db_name) as data_conn:
                for path in DATA_FILES:
                    if not path.exists():
                        print(f"  跳过（不存在）：{path.name}")
                        continue
                    execute_file(data_conn, path)
                    print(f"  导入 {path.name}")

        with connect(settings, settings.db_name) as check_conn:
            rows = list_tables(check_conn)

    print(f"\n完成：共 {len(rows)} 张表")
    if len(rows) != EXPECTED_TABLES:
        print(f"注意：预期 {EXPECTED_TABLES} 张表，实际 {len(rows)} 张，请核对")
    for name, count in rows:
        print(f"  {name:<28} {count:>8} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
