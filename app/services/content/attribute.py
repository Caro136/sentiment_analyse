"""主题归属回填：把已打标的内容挂到监测主题上（FR-08）。

**为什么需要它**：`content_analysis.topic_id` 是主题筛选（FR-42/43）唯一的依据，
但打标本身不决定归属——归属由「内容命中了哪个主题的关键词」决定。
两边接上之前，主题筛选永远是空结果，而**空结果不会报错**，很容易被当成数据缺口。

归属规则与接入层一致：拿主题的 `source_keyword` 走**双向模糊匹配**
（`search_raw_hits` 用的就是这套条件）。因此同一个内容可能命中多个主题时，
按主题的创建顺序先到先得，并记 `--force` 是否覆盖既有归属。

用法：
    python -m app.services.content.attribute            # 只填 topic_id 为空的内容
    python -m app.services.content.attribute --force    # 重新归属（会覆盖既有值）
    python -m app.services.content.attribute --dry-run  # 只报告将要改动的条数

新内容的归属应当由打标链路直接带上（`analytics.service.analyze_contents(topic_id=...)`），
本命令是给「先打标、后建主题」的历史数据补账用的。
"""

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.services.monitoring.repository import SqlMonitoringRepository
from engines.common.db.session import dispose_engine, session_scope
from engines.contracts.enums import TopicStatus
from engines.ingestion.reader import MATCH_TOPIC, search_raw_hits

__all__ = ["attribute_topics", "main"]


async def attribute_topics(*, force: bool = False, dry_run: bool = False) -> list[dict]:
    """为每个启用中的主题回填内容归属，返回逐主题的报告。"""
    reports: list[dict] = []

    async with session_scope() as session:
        repo = SqlMonitoringRepository(session)
        _, topics = await repo.list_topics(page=1, page_size=200,
                                           status=TopicStatus.ENABLED.value)

        for topic in topics:
            report = {"topic_id": topic.topic_id, "name": topic.name,
                      "source_keyword": topic.source_keyword, "candidates": 0,
                      "updated": 0}
            reports.append(report)

            if not topic.source_keyword:
                # 没有主题关键词就召不到任何内容——这通常意味着词包为空
                report["note"] = "主题没有 source_keyword，跳过"
                continue

            # match="topic"：按 source_keyword 判定归属，**不是**按正文关键词检索
            # （两者语义不同，写错不报错、只是归属数少得离谱）
            hits = await search_raw_hits(session=session, keyword=topic.source_keyword,
                                        match=MATCH_TOPIC)
            report["candidates"] = len(hits)
            if not hits or dry_run:
                continue

            uids = [hit.content_uid for hit in hits]
            condition = "" if force else " AND (topic_id IS NULL OR topic_id = '')"
            total = 0
            for start in range(0, len(uids), 500):
                batch = uids[start:start + 500]
                params = {f"u{i}": uid for i, uid in enumerate(batch)}
                params["topic_id"] = topic.topic_id
                placeholders = ", ".join(f":u{i}" for i in range(len(batch)))
                result = await session.execute(text(
                    f"UPDATE content_analysis SET topic_id = :topic_id "
                    f"WHERE content_uid IN ({placeholders}){condition}"), params)
                total += result.rowcount or 0
            report["updated"] = total

    return reports


def _print(reports: list[dict], *, dry_run: bool) -> None:
    print(f"{'主题':<22}{'关键词':<18}{'候选内容':>8}{'已归属' if not dry_run else '待归属':>10}")
    for report in reports:
        print(f"{report['name']:<22}{report['source_keyword'] or '-':<18}"
              f"{report['candidates']:>8}{report.get('updated', 0):>10}"
              + (f"   {report['note']}" if report.get("note") else ""))


async def _run(*, force: bool, dry_run: bool) -> list[dict]:
    """跑一轮并在**同一事件循环内**释放连接池。

    池里的连接绑定创建它的循环，跨 `asyncio.run()` 去关会报
    「Event loop is closed」（T-02 的教训，T-19 又踩过一次）。
    """
    try:
        return await attribute_topics(force=force, dry_run=dry_run)
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填内容到监测主题的归属")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已有归属（默认只填 topic_id 为空的内容）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args(argv)

    try:
        reports = asyncio.run(_run(force=args.force, dry_run=args.dry_run))
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    _print(reports, dry_run=args.dry_run)
    if args.dry_run:
        print("\n（--dry-run：未写库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
