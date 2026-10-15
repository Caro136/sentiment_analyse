"""数据留存策略（FR-74）：按配置周期清理**分析结果**。

配置项在运行配置白名单里（`ANALYSIS_RETENTION_DAYS` / `DASHBOARD_RETENTION_DAYS`），
管理员可在 `/api/v1/config` 热更新；本模块是执行者（CLI）。

**原始区永远不清理**，这条不是偷懒而是设计约束：9 张原始表是数据生成器的交付物，
ADR-03 规定本项目只读。FR-74 原文写的是「原始内容与分析结果的保留周期」，
但「删除原始内容」与「原始表只读」直接冲突——遇到这种冲突应当显式说明并让读者
知情，而不是悄悄选一条路：

    原始数据的「留存周期」由**重新生成数据集**来管理
    （`python data/generator/generate.py` 覆盖写入），不由本项目删除。

用法：
    python -m app.services.system.retention                # 只看（默认 dry-run）
    python -m app.services.system.retention --apply        # 真正删除
    python -m app.services.system.retention --days 30      # 覆盖配置的周期
"""

import argparse
import sys
import time

from sqlalchemy import text

from engines.common.db.session import dispose_engine, session_scope
from engines.contracts.config import get_settings

__all__ = ["RETENTION_TARGETS", "preview", "apply_retention", "main"]

# 可清理的分析区表 → 用哪个时间列判断「过期」
# 只列**可独立重算**的结果：内容标签与多模态产物。实体/事件/聚类不在此列——
# 清掉标签却留着图谱会得到互相矛盾的数据，那属于「整体重跑」而不是「按周期清理」。
# `dashboard_metrics` 也不列：它每 60 秒被覆盖一次，旧行本就不存在。
RETENTION_TARGETS: dict[str, str] = {
    "content_analysis": "analyzed_at",
    "modality_asset": "created_at",
}


async def preview(*, days: int) -> dict[str, int]:
    """统计各表超出保留期的行数（不删除）。"""
    cutoff = int(time.time()) - days * 86400
    counts: dict[str, int] = {}
    async with session_scope() as session:
        for table, column in RETENTION_TARGETS.items():
            counts[table] = int((await session.execute(
                text(f"SELECT COUNT(*) FROM `{table}` WHERE `{column}` < :cutoff"),
                {"cutoff": cutoff})).scalar() or 0)
    return counts


async def apply_retention(*, days: int) -> dict[str, int]:
    """删除超出保留期的分析结果，返回逐表删除行数。"""
    cutoff = int(time.time()) - days * 86400
    deleted: dict[str, int] = {}
    async with session_scope() as session:
        for table, column in RETENTION_TARGETS.items():
            result = await session.execute(
                text(f"DELETE FROM `{table}` WHERE `{column}` < :cutoff"),
                {"cutoff": cutoff})
            deleted[table] = int(result.rowcount or 0)
    return deleted


def _days_from_settings() -> int:
    settings = get_settings()
    return max(1, int(getattr(settings, "analysis_retention_days", 180)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按留存周期清理分析结果")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只看）")
    parser.add_argument("--days", type=int, default=None, help="覆盖配置的保留天数")
    args = parser.parse_args(argv)

    days = args.days or _days_from_settings()

    async def _run():
        try:
            if args.apply:
                return await apply_retention(days=days), True
            return await preview(days=days), False
        finally:
            await dispose_engine()

    try:
        counts, applied = _run_sync(_run)
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print(f"留存周期：{days} 天（{'已执行删除' if applied else '只看，未删除'}）")
    print(f"{'表':<22}{'行数'}")
    for table, count in counts.items():
        print(f"{table:<22}{count}")
    total = sum(counts.values())
    if not applied and total:
        print(f"\n共 {total} 行超出保留期，加 --apply 执行删除。")
    print("\n原始区不受本命令影响：原始数据的「留存」由重新生成数据集管理"
          "（data/generator/generate.py），本项目对原始表只读。")
    return 0


def _run_sync(coro_factory):
    import asyncio

    return asyncio.run(coro_factory())


if __name__ == "__main__":
    sys.exit(main())
