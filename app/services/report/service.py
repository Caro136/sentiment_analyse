"""报告中心用例（FR-57~FR-63）。

**生成是异步长任务**：实测一份报告约 90 秒（T-17 的真实数据）。因此：

```
POST /reports  → 落一条 status=generating 的报告记录 → 起后台任务 → 立即 202 返回
后台任务       → report_engine 生成 → 落盘 md/html → 写 report_file → 更新状态 → 发事件
GET  /reports/{id} → 预览（读 report_file.content_md，不依赖磁盘）
```

三个关键取舍：

1. **正文同时存库**（`content_md`）：预览与导出不读磁盘，产物目录被清理或迁移后
   报告依然可看可下。磁盘文件仍然是「交付物」，两者互为备份。
2. **进度走事件总线**（`ROLE_PROGRESS`，角色是 `report`）：前端已有的进度条与
   SSE 机制直接复用，不必为报告再开一套轮询协议。T-18 的 SSE 广播会把它推给前端。
3. **PDF 不在服务端渲染**（D-07）：导出提供 Markdown 与自包含 HTML，
   PDF 由浏览器打印生成。请求 `file_type=pdf` 时**明确拒绝并说明替代路径**，
   而不是偷偷返回 HTML——那会让调用方以为拿到了 PDF。
"""

import asyncio
import time

from loguru import logger

from app.errors import conflict, invalid_request, not_found
from app.services.audit import audit, note_change
from app.services.audit.context import note_target
from app.services.report.repository import (
    ReportFileRecord,
    ReportRecord,
    ReportRepository,
    new_file_id,
    new_report_id,
    new_task_id,
    snapshot,
)
from engines.common.eventing.publishers import (
    publish_role_error,
    publish_role_progress,
    publish_role_result,
)
from engines.contracts.enums import AuditAction, ReportStatus, ReportType
from engines.contracts.roles import ROLE_REPORT
from engines.report_engine.engine import generate_report

# 可下载的产物类型。**不含 pdf**：D-07 决定 PDF 由浏览器打印生成（见模块文档）。
DOWNLOAD_TYPES = ("md", "html")


# ---------------------------------------------------------------------------
# query 解析：报告的「主题词」用于两处
# ---------------------------------------------------------------------------

def report_title(query: str, *, periodic: bool = False) -> str:
    """报告标题。由 query 确定性派生——**判重也靠它**（见 `create_report`）。"""
    return f"舆情周期简报：{query}" if periodic else f"舆情研判报告：{query}"


async def resolve_query(*, event_id: str | None, topic_id: str | None,
                        explicit: str | None, event_repo=None, monitoring_repo=None,
                        session=None) -> tuple[str, str, str]:
    """把「事件 / 主题 / 显式主题词」解析成 (query, report_type, title)。

    `query` 有两个用途，**必须是同一个值**：① 报告引擎按它加载三方产物
    （产物文件名里带的就是它）；② 报告标题与后续按主题检索。
    因此这里统一从事件标题或主题名称取，而不是让调用方各传各的。
    """
    if explicit and explicit.strip():
        query = explicit.strip()
        return query, ReportType.EVENT.value, report_title(query)

    if event_id:
        if event_repo is None:
            raise invalid_request("缺少事件仓储，无法解析事件标题")
        event = await event_repo.get_event(event_id)
        if event is None:
            raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")
        return event.title, ReportType.EVENT.value, report_title(event.title)

    if topic_id:
        if monitoring_repo is None:
            raise invalid_request("缺少监测配置仓储，无法解析主题名称")
        topic = await monitoring_repo.get_topic(topic_id)
        if topic is None:
            raise not_found(f"主题不存在：{topic_id}", detail=f"topic_id={topic_id}")
        return (topic.name, ReportType.PERIODIC.value,
                report_title(topic.name, periodic=True))

    raise invalid_request("需要提供 event_id、topic_id 或 query 之一")


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def to_view(record: ReportRecord, *, files: list[ReportFileRecord] | None = None,
            include_content: bool = False) -> dict:
    """报告视图。`include_content` 控制是否带正文（列表不带）。"""
    markdown = ""
    if files:
        markdown = next((f.content_md or "" for f in files if f.file_type == "md"), "")
    view = {
        "report_id": record.report_id,
        "event_id": record.event_id,
        "topic_id": record.topic_id,
        "report_type": record.report_type,
        "type_label": ReportType(record.report_type).label,
        "title": record.title,
        "status": record.status,
        "status_label": ReportStatus(record.status).label,
        "error_message": record.error_message,
        "generated_at": record.generated_at,
        "created_by": record.created_by,
        "created_at": record.created_at,
        "chars": len(markdown),
        "files": [{"file_type": f.file_type, "file_path": f.file_path,
                   "size_bytes": f.size_bytes} for f in (files or [])],
        "download_types": list(DOWNLOAD_TYPES),
    }
    if include_content:
        view["content_md"] = markdown
    return view


async def list_reports(repo: ReportRepository, *, page: int = 1, page_size: int = 20,
                       topic_id: str | None = None, event_id: str | None = None,
                       status: str | None = None) -> tuple[int, list[dict]]:
    """报告列表（FR-57）。**不返回正文**——列表页不需要，一页报告正文可能上百 KB。"""
    total, rows = await repo.list_reports(page=page, page_size=page_size,
                                         topic_id=topic_id, event_id=event_id,
                                         status=status)
    views = []
    for record in rows:
        files = await repo.list_files(record.report_id)
        views.append(to_view(record, files=files))
    return total, views


async def get_report_detail(repo: ReportRepository, report_id: str, *,
                            include_content: bool = True) -> dict:
    """报告详情与在线预览（FR-60）。正文取自库，不读磁盘。"""
    record = await repo.get_report(report_id)
    if record is None:
        raise not_found(f"报告不存在：{report_id}", detail=f"report_id={report_id}")
    files = await repo.list_files(report_id)
    return to_view(record, files=files, include_content=include_content)


# ---------------------------------------------------------------------------
# 发起生成
# ---------------------------------------------------------------------------

@audit(AuditAction.REPORT_GENERATE, target="report_create")
async def create_report(repo: ReportRepository, *, actor: object, event_id: str | None = None,
                        topic_id: str | None = None, query: str | None = None,
                        event_repo=None, monitoring_repo=None) -> tuple[dict, dict]:
    """创建报告任务（FR-58）。返回 (报告视图, 任务输入)。

    调用方拿到任务输入后自行起后台协程（`run_generation`）——
    这样测试可以直接 await 它，不必和后台任务赛跑。
    """
    # 先判重再解析目标：重复提交时不该为了「给出更好的错误信息」去多查两次库。
    # 只给 query 的路径没有 event/topic 可匹配，用**由 query 派生的标题**兜住——
    # 否则同一个主题连点两次按钮会真的跑两份报告（各约 90 秒、上万 token）。
    if query and query.strip():
        expected_title = report_title(query.strip())
        existing = await repo.find_active(title=expected_title)
    else:
        existing = await repo.find_active(event_id=event_id, topic_id=topic_id)

    if existing is not None:
        raise conflict(f"该目标已有正在生成的报告：{existing.report_id}",
                       detail=f"report_id={existing.report_id}")

    resolved, report_type, title = await resolve_query(
        event_id=event_id, topic_id=topic_id, explicit=query,
        event_repo=event_repo, monitoring_repo=monitoring_repo)

    record = await repo.create_report(ReportRecord(
        report_id=new_report_id(), event_id=event_id, topic_id=topic_id,
        report_type=report_type, title=title, status=ReportStatus.GENERATING.value,
        error_message=None, generated_at=None,
        created_by=getattr(actor, "user_id", None), created_at=int(time.time())))

    task_id = new_task_id()
    await repo.start_task(task_id=task_id, task_type="report",
                          input_summary=f"query={resolved} type={report_type}")
    # report_id 是这里才生成的，装饰器的模板渲染不出来 → 显式登记审计目标
    note_target(f"report:{record.report_id}")
    note_change(after=snapshot(record))
    logger.info("报告任务已创建 report_id={} query={} 操作人={}",
                record.report_id, resolved, getattr(actor, "user_id", "-"))

    return to_view(record), {"report_id": record.report_id, "query": resolved,
                             "task_id": task_id}


async def run_generation(repo: ReportRepository, *, report_id: str, query: str,
                         task_id: str) -> None:
    """后台生成：跑引擎 → 落库 → 更新状态 → 发进度事件（FR-58/62）。

    失败（材料不全、模型不可用、落盘失败）都写进 `report.error_message` 并置
    `status=failed`——前端据此显示原因与「重试」入口（FR-62 的验收要求）。

    ⚠️ **不要把这个协程挂在请求的会话上**（正如实踩过）：请求级 `session_scope`
    在响应发出前就已提交并关闭，后台任务再往里写会**静默丢失**——报告落盘了、
    日志写着「生成完成」，但 `report_file` 是空的、状态永远停在 `generating`。
    调用方请用 `run_generation_task`（它自己开会话）。
    """
    publish_role_progress(ROLE_REPORT, "formatting", f"正在生成报告：{query}", 50)
    started = time.monotonic()

    try:
        output = await generate_report(query)
    except Exception as exc:  # noqa: BLE001 —— 引擎内部已兜底，这里防的是意外
        logger.opt(exception=exc).error("报告生成异常 report_id={}", report_id)
        await _mark_failed(repo, report_id, task_id,
                           f"{type(exc).__name__}: {exc}", started)
        return

    if not output.ok:
        await _mark_failed(repo, report_id, task_id,
                           output.error or "报告生成失败", started)
        return

    await _store_output(repo, report_id=report_id, output=output)
    await repo.update_report(report_id, {
        "status": ReportStatus.GENERATED.value,
        "generated_at": int(time.time()),
        "error_message": None})
    await repo.finish_task(task_id, status="success", duration_ms=_elapsed_ms(started))

    publish_role_progress(ROLE_REPORT, "completed", "报告已生成", 100)
    publish_role_result(ROLE_REPORT, output.html_path or output.markdown_path)
    logger.info("报告生成完成 report_id={} {} 字 / 耗时 {:.1f}s",
                report_id, len(output.markdown), time.monotonic() - started)


async def _store_output(repo: ReportRepository, *, report_id: str, output) -> None:
    """把产物写入 `report_file`：md 与 html 各一行，正文存库。"""
    now = int(time.time())
    await repo.add_file(ReportFileRecord(
        file_id=new_file_id(), report_id=report_id, file_type="md",
        file_path=output.markdown_path or "",
        content_md=output.markdown, size_bytes=len(output.markdown.encode("utf-8")),
        created_at=now))
    if output.html:
        await repo.add_file(ReportFileRecord(
            file_id=new_file_id(), report_id=report_id, file_type="html",
            file_path=output.html_path or "",
            content_md=output.html, size_bytes=len(output.html.encode("utf-8")),
            created_at=now))


def _elapsed_ms(started: float) -> int:
    """毫秒耗时（`run_generation` 用 `time.monotonic()` 起算，不受系统时间调整影响）。"""
    return max(0, int((time.monotonic() - started) * 1000))


async def _mark_failed(repo: ReportRepository, report_id: str, task_id: str,
                       error: str, started: float) -> None:
    await repo.update_report(report_id, {"status": ReportStatus.FAILED.value,
                                        "error_message": error[:500]})
    await repo.finish_task(task_id, status="failed", error=error,
                           duration_ms=_elapsed_ms(started))
    publish_role_error(ROLE_REPORT, error)
    logger.warning("报告生成失败 report_id={}：{}（耗时 {:.1f}s）",
                   report_id, error, time.monotonic() - started)


# ---------------------------------------------------------------------------
# 导出（FR-61 / D-07）
# ---------------------------------------------------------------------------

async def get_download_payload(repo: ReportRepository, report_id: str, *,
                               file_type: str) -> tuple[str, str, str]:
    """取导出内容。返回 (文件名, 正文, MIME)。

    **不接受 pdf**：D-07 决定 PDF 由浏览器打印生成，服务端不做渲染。
    这里明确报错并给出替代路径，而不是返回一份 HTML 让调用方以为拿到了 PDF。
    """
    if file_type == "pdf":
        raise invalid_request(
            "PDF 由浏览器打印生成（D-07）",
            detail="请下载 html 后在浏览器中打印，或使用报告预览页的打印入口；"
                   "也可直接下载 md")
    if file_type not in DOWNLOAD_TYPES:
        raise invalid_request(f"不支持的文件类型：{file_type}",
                              detail="可选：" + "/".join(DOWNLOAD_TYPES))

    record = await repo.get_report(report_id)
    if record is None:
        raise not_found(f"报告不存在：{report_id}", detail=f"report_id={report_id}")
    if record.status != ReportStatus.GENERATED.value:
        raise conflict(f"报告尚未生成完成（当前状态：{record.status}）",
                       detail=f"status={record.status}")

    files = await repo.list_files(report_id)
    target = next((f for f in files if f.file_type == file_type), None)
    if target is None or not (target.content_md or "").strip():
        raise not_found(f"报告缺少 {file_type} 产物",
                        detail=f"report_id={report_id} file_type={file_type}")

    extension = file_type
    mime = "text/markdown; charset=utf-8" if file_type == "md" else "text/html; charset=utf-8"
    filename = f"{_safe_filename(record.title)}.{extension}"
    return filename, target.content_md or "", mime


def _safe_filename(title: str) -> str:
    """把标题变成可用的文件名（去掉路径分隔符与非法字符）。"""
    import re

    return re.sub(r'[<>:"/\\|?*\s]+', "_", title).strip("_")[:80] or "report"


async def run_generation_task(*, report_id: str, query: str, task_id: str) -> None:
    """后台任务的入口：**自己开一个会话**跑完整次生成。

    这是后台任务唯一正确的用法——请求级会话在响应发出前就提交关闭了，
    复用它写库会静默丢失（见 `run_generation` 的告警）。
    """
    from app.services.report.repository import SqlReportRepository
    from engines.common.db.session import session_scope

    async with session_scope() as session:
        await run_generation(SqlReportRepository(session), report_id=report_id,
                             query=query, task_id=task_id)


async def recover_interrupted(repo: ReportRepository | None = None) -> dict:
    """启动时收尾：把上次进程中断留下的在途任务标记为失败。

    **不做这件事的后果是静默的**：报告停在 `generating`，前端永远显示「生成中」，
    而且判重会把该事件**永久挡住**（「已有正在生成的报告」，却永远等不到完成）。

    判据是「进程重启」——进程内后台任务随进程消失，因此启动时所有 `generating`
    都是上一轮的遗骸。`task_run` 的 `running` 行同理（运维页照着它显示「正在运行」，
    排查方向从一开始就是错的）。

    `repo` 留给测试注入；生产由 lifespan 直接调用（自开会话）。
    """
    from app.services.report.repository import SqlReportRepository
    from engines.common.db.session import session_scope

    reason = "服务重启，任务中断，请重新发起"

    async def _recover(target: ReportRepository) -> dict:
        interrupted: list[str] = []
        for record in (await target.list_reports(
                page=1, page_size=200, status=ReportStatus.GENERATING.value))[1]:
            await target.update_report(record.report_id, {
                "status": ReportStatus.FAILED.value,
                "error_message": reason,
            })
            interrupted.append(record.report_id)
        interrupted_tasks = await target.mark_interrupted_tasks(reason)
        return {"interrupted_reports": interrupted, "interrupted_tasks": interrupted_tasks}

    if repo is not None:
        return await _recover(repo)
    async with session_scope() as session:
        return await _recover(SqlReportRepository(session))


def as_task(coro) -> asyncio.Task:
    """把生成协程挂成后台任务（路由用）。

    单独抽出来是为了让「发起」与「执行」解耦：测试直接 await 协程，
    生产环境走后台任务，两者跑的是同一段代码。
    """
    return asyncio.create_task(coro)


__all__ = [
    "list_reports",
    "get_report_detail",
    "create_report",
    "run_generation",
    "run_generation_task",
    "recover_interrupted",
    "get_download_payload",
    "as_task",
]
