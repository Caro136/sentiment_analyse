"""把磁盘上已有、但未登记进库的报告产物补登记（FR-57 的存量补账）。

**为什么需要它**：报告产物是「先落盘、后写库」的（`report_engine` 落文件，
服务层再登记）。T-17 时期的报告是直接调引擎生成的，从未经过报告中心；
T-27 早期还有一批报告的写库因复用请求会话而丢失（见 `service.run_generation`
的告警）。磁盘上有产物、库里没有记录的报告，在报告中心是查不到的。

补登记是**幂等**的：按 `report_file.file_path` 判断是否已登记，重复执行不会产生副本。

用法：
    python -m app.services.report.backfill            # 补登记未登记的产物
    python -m app.services.report.backfill --dry-run  # 只报告会补哪些

补登记的记录 `created_by` 为空（系统补账，不是某个用户发起的），
`generated_at` 取文件名里的时间戳——那是产物真正生成的时间，不是补登记时间。
"""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.services.monitoring.repository import SqlMonitoringRepository
from app.services.report.repository import (
    ReportFileRecord,
    ReportRecord,
    SqlReportRepository,
    new_file_id,
    new_report_id,
)
from engines.common.db.session import dispose_engine, session_scope
from engines.common.logging import logger
from engines.contracts.config import DEFAULT_REPORT_DIR
from engines.contracts.enums import ReportStatus, ReportType
from engines.report_engine.loader import _safe_name  # noqa: PLC2701 —— 与落盘命名保持同一规则

__all__ = ["scanned_report", "parse_report_name", "backfill", "main"]

# 产物文件名：`report_{安全化主题}_{时间戳}.md`
_NAME_PATTERN = re.compile(r"^report_(?P<name>.+)_(?P<ts>\d{9,})\.md$")


@dataclass(frozen=True)
class ScannedReport:
    """磁盘上的一份报告产物。"""

    query: str
    generated_at: int
    markdown_path: Path
    html_path: Path | None
    size_bytes: int


def parse_report_name(filename: str) -> tuple[str, int] | None:
    """从文件名解析 (主题词, 生成时间戳)。不符合命名规则返回 None。

    时间戳是产物生成时刻，比「文件修改时间」可靠——复制/迁移会改后者。
    """
    matched = _NAME_PATTERN.match(filename)
    if not matched:
        return None
    # 文件名里的主题词做过安全化（空格与特殊字符替换成下划线），无法还原原文；
    # 但报告标题与检索用的一直是这个安全化后的字符串，保持一致即可
    return matched.group("name"), int(matched.group("ts"))


def scanned_report(directory: Path | None = None) -> list[ScannedReport]:
    """扫描产物目录，返回按生成时间正序的报告列表。"""
    root = directory or DEFAULT_REPORT_DIR
    if not root.is_dir():
        return []

    found: list[ScannedReport] = []
    for markdown_path in root.glob("report_*.md"):
        parsed = parse_report_name(markdown_path.name)
        if parsed is None:
            logger.warning("报告产物命名不符合规则，跳过：{}", markdown_path.name)
            continue
        query, generated_at = parsed
        html_path = markdown_path.with_suffix(".html")
        found.append(ScannedReport(
            query=query, generated_at=generated_at, markdown_path=markdown_path,
            html_path=html_path if html_path.is_file() else None,
            size_bytes=markdown_path.stat().st_size))
    found.sort(key=lambda item: item.generated_at)
    return found


async def backfill(*, dry_run: bool = False,
                   directory: Path | None = None) -> list[dict]:
    """补登记未登记的产物，返回逐条结果。"""
    candidates = scanned_report(directory)
    results: list[dict] = []

    async with session_scope() as session:
        repo = SqlReportRepository(session)
        monitoring = SqlMonitoringRepository(session)

        # 已登记的文件路径集合（按 posix 形式归一，避免 Windows 反斜杠差异）
        registered: set[str] = set()
        for page in range(1, 21):
            total, reports = await repo.list_reports(page=page, page_size=100)
            for report in reports:
                for item in await repo.list_files(report.report_id):
                    registered.add(_normalize(item.file_path))
            if page * 100 >= total:
                break

        # 主题名 → 主题ID：能对上就把报告挂到主题上（报告中心按主题检索要用）
        _, topics = await monitoring.list_topics(page=1, page_size=200)
        topic_by_name = {topic.name: topic.topic_id for topic in topics}

        for item in candidates:
            path_key = _normalize(str(item.markdown_path))
            if path_key in registered:
                results.append({"query": item.query, "action": "skip",
                                "reason": "已登记", "path": str(item.markdown_path)})
                continue

            topic_id = topic_by_name.get(item.query)
            results.append({
                "query": item.query, "action": "backfill" if not dry_run else "dry-run",
                "generated_at": item.generated_at, "topic_id": topic_id,
                "markdown": str(item.markdown_path),
                "html": str(item.html_path) if item.html_path else "",
                "size_bytes": item.size_bytes,
            })
            if dry_run:
                continue

            record = await repo.create_report(ReportRecord(
                report_id=new_report_id(), event_id=None, topic_id=topic_id,
                report_type=ReportType.PERIODIC.value if topic_id else ReportType.EVENT.value,
                title=f"舆情研判报告：{item.query}",
                status=ReportStatus.GENERATED.value, error_message=None,
                generated_at=item.generated_at, created_by=None,
                created_at=item.generated_at))

            markdown = item.markdown_path.read_text(encoding="utf-8")
            await repo.add_file(ReportFileRecord(
                file_id=new_file_id(), report_id=record.report_id, file_type="md",
                file_path=str(item.markdown_path), content_md=markdown,
                size_bytes=item.size_bytes, created_at=item.generated_at))

            if item.html_path is not None:
                html = item.html_path.read_text(encoding="utf-8")
                await repo.add_file(ReportFileRecord(
                    file_id=new_file_id(), report_id=record.report_id, file_type="html",
                    file_path=str(item.html_path), content_md=html,
                    size_bytes=item.html_path.stat().st_size,
                    created_at=item.generated_at))

            registered.add(path_key)
            logger.info("补登记报告 {} → {}（{} 字）", item.query, record.report_id,
                        len(markdown))

    return results


def _normalize(path: str) -> str:
    """路径归一化为**文件名**（小写）。

    不能按完整路径比对：库里存的是相对项目根的路径（`docs\\reports\\x.md`，
    由引擎落盘时写入），而扫描得到的是绝对路径——直接比会**全部判为未登记**，
    于是每次执行都补一遍，报告中心里出现一串重复条目（实测踩过）。
    文件名里含主题与生成时间戳，本身已经唯一，取 basename 足够。
    """
    return path.replace("\\", "/").rsplit("/", 1)[-1].lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="补登记磁盘上未入库的报告产物")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    parser.add_argument("--dir", default=None, help="产物目录（默认 docs/reports）")
    args = parser.parse_args(argv)

    async def _run() -> list[dict]:
        try:
            return await backfill(dry_run=args.dry_run,
                                  directory=Path(args.dir) if args.dir else None)
        finally:
            # 同一事件循环内释放连接池（T-02 的教训）
            await dispose_engine()

    try:
        results = asyncio_run(_run())
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print(f"{'主题词':<24}{'动作':<10}{'生成时间':<20}{'大小'}")
    for item in results:
        when = ("-" if not item.get("generated_at") else
                _format_ts(item["generated_at"]))
        size = f"{item['size_bytes']}B" if item.get("size_bytes") else "-"
        print(f"{item['query'][:22]:<24}{item['action']:<10}{when:<20}{size}"
              + (f"  {item.get('reason', '')}" if item.get("reason") else ""))
    if args.dry_run:
        print("\n（--dry-run：未写库）")
    return 0


def _format_ts(value: int) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))


def asyncio_run(coro):
    """延迟导入 asyncio（保持模块导入期无副作用）。"""
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    sys.exit(main())
