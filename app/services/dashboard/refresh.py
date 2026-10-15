"""手动刷新大盘指标（补算与排障用）。

服务有后台任务在跑，这里给的是**离线/排障入口**：

    python -m app.services.dashboard.refresh              # 全局 + 全部启用主题
    python -m app.services.dashboard.refresh --topic tp1  # 只刷某个主题
    python -m app.services.dashboard.refresh --hours 168  # 拉长取数窗口

典型用途：服务刚启动、或刚导入一批新数据（后台任务还没到点）时，
不必等 60 秒就能让大盘数字是最新的。
"""

import argparse
import asyncio
import sys

from app.services.dashboard import service as dashboard_service
from app.services.dashboard.repository import SqlDashboardRepository
from app.services.content.repository import SqlContentRepository
from app.services.event.repository import SqlEventRepository
from app.services.monitoring.repository import SqlMonitoringRepository
from engines.common.db.session import dispose_engine, session_scope
from engines.contracts.enums import TopicStatus


async def _run(*, topic_id: str | None, hours: int | None) -> dict[str, dict]:
    """刷新并返回各指标的摘要（**在同一事件循环内**释放连接池，见 T-02 的教训）。"""
    try:
        async with session_scope() as session:
            repo = SqlDashboardRepository(session)
            scopes = [topic_id] if topic_id else [None]
            if not topic_id:
                _, topics = await SqlMonitoringRepository(session).list_topics(
                    page=1, page_size=200, status=TopicStatus.ENABLED.value)
                scopes += [topic.topic_id for topic in topics]

            summary: dict[str, dict] = {}
            for scope in scopes:
                snapshots = await dashboard_service.refresh_scope(
                    repo, scope=scope,
                    event_repo=SqlEventRepository(session),
                    content_repo=SqlContentRepository(session),
                    session=session, hours=hours)
                overview = next((s.payload for s in snapshots
                                 if s.metric_key == "overview"), {})
                summary[scope or "all"] = {
                    "总声量": overview.get("total_volume"),
                    "负面率": overview.get("negative_ratio"),
                    "S/A 级内容": overview.get("high_risk_content_count"),
                    "S/A 级事件": overview.get("high_risk_event_count"),
                    "待处置工单": overview.get("open_ticket_count"),
                    "指标数": len(snapshots),
                }
            return summary
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="刷新大盘指标物化")
    parser.add_argument("--topic", default=None, help="只刷指定主题（默认全部）")
    parser.add_argument("--hours", type=int, default=None, help="取数窗口小时数")
    args = parser.parse_args(argv)

    try:
        summary = asyncio.run(_run(topic_id=args.topic, hours=args.hours))
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print(f"{'范围':<26}{'总声量':>8}{'负面率':>9}{'S/A内容':>9}{'S/A事件':>9}{'待处置':>8}")
    for scope, item in summary.items():
        ratio = item["负面率"]
        print(f"{scope:<26}{item['总声量']:>8}"
              f"{(f'{ratio * 100:.1f}%' if ratio is not None else '-'):>9}"
              f"{item['S/A 级内容']:>9}{item['S/A 级事件'] if item['S/A 级事件'] is not None else '-':>9}"
              f"{item['待处置工单']:>8}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
