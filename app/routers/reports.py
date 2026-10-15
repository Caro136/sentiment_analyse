"""报告中心路由（API-25~28，FR-57~FR-63）。

四个接口的分工：

| 接口 | 语义 |
|------|------|
| `POST /reports` | 发起生成（异步任务，立即 202 返回 `report_id`） |
| `GET /reports` | 列表（不含正文） |
| `GET /reports/{id}` | 详情与在线预览（含 Markdown 原文） |
| `GET /reports/{id}/download` | 导出 Markdown / 自包含 HTML |

**PDF 走浏览器打印**（D-07）：`file_type=pdf` 会返回 422 并说明替代路径，
而不是悄悄返回一份 HTML——那会让调用方以为拿到了 PDF。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.schemas.report import ReportCreateRequest, ReportDetailView, ReportQuery, ReportView
from app.security import require_permission
from app.services.audit import audit, note_change
from app.services.auth.repository import UserRecord
from app.services.event.repository import SqlEventRepository
from app.services.monitoring.repository import SqlMonitoringRepository
from app.services.report import service
from app.services.report.repository import SqlReportRepository
from engines.common.db.session import get_db_session
from engines.contracts.enums import AuditAction, ReportStatus
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

_ReadReport = Annotated[UserRecord, Depends(require_permission(Permission.REPORT_READ))]
_GenerateReport = Annotated[UserRecord, Depends(require_permission(Permission.REPORT_GENERATE))]
_ExportReport = Annotated[UserRecord, Depends(require_permission(Permission.REPORT_EXPORT))]


async def get_report_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlReportRepository:
    return SqlReportRepository(session)


@router.get("", response_model=PageResult[ReportView], summary="报告列表")
async def list_reports(
    _actor: _ReadReport,
    repo=Depends(get_report_repository),
    topic_id: str | None = Query(default=None, max_length=64),
    event_id: str | None = Query(default=None, max_length=64),
    status: ReportStatus | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[ReportView]:
    """按主题/事件/状态筛选，按创建时间倒序（FR-57）。列表不返回正文。"""
    query = ReportQuery(page=page, page_size=page_size, topic_id=topic_id,
                        event_id=event_id, status=status)
    total, items = await service.list_reports(
        repo, page=query.page, page_size=query.page_size, topic_id=query.topic_id,
        event_id=query.event_id, status=query.status.value if query.status else None)
    return paginate(total, [ReportView(**item) for item in items], query.page, query.page_size)


@router.post("", response_model=ReportView, status_code=202, summary="发起报告生成")
async def create_report(
    payload: ReportCreateRequest, actor: _GenerateReport,
    repo=Depends(get_report_repository),
    session: AsyncSession = Depends(get_db_session),
) -> ReportView:
    """发起一次报告生成（FR-58）。

    **异步**：立即返回 `status=generating`，生成进度经 SSE 推送
    （`role_progress`，角色为 report）；完成后列表与详情可见产物。
    材料不全时**不生成半成品**：失败原因写入 `error_message`（FR-62）。
    """
    view, task = await service.create_report(
        repo, actor=actor, event_id=payload.event_id, topic_id=payload.topic_id,
        query=payload.query, event_repo=SqlEventRepository(session),
        monitoring_repo=SqlMonitoringRepository(session))

    # 后台执行。**必须走 run_generation_task**：它自己开会话，
    # 而请求级会话在响应发出前就提交关闭了，后台任务复用它写库会静默丢失
    # （实测：报告落盘成功、日志写着完成，但库里没有产物、状态停在 generating）。
    service.as_task(service.run_generation_task(
        report_id=task["report_id"], query=task["query"], task_id=task["task_id"]))
    return ReportView(**view)


@router.get("/{report_id}", response_model=ReportDetailView, summary="报告详情与预览")
async def get_report(report_id: str, _actor: _ReadReport,
                     repo=Depends(get_report_repository)) -> ReportDetailView:
    """在线预览（FR-60）：返回 Markdown 原文，正文取自库、不读磁盘。"""
    return ReportDetailView(**await service.get_report_detail(repo, report_id))


@audit(AuditAction.REPORT_EXPORT, target="report:{report_id}")
async def _export(repo, report_id: str, *, file_type: str, actor: UserRecord
                  ) -> tuple[str, str, str]:
    """导出动作的审计包装（下载本身由路由直接返回响应）。"""
    note_change(after={"file_type": file_type, "operator": actor.user_id})
    return await service.get_download_payload(repo, report_id, file_type=file_type)


@router.get("/{report_id}/download", summary="下载报告（Markdown / HTML）")
async def download_report(
    report_id: str, _actor: _ExportReport,
    repo=Depends(get_report_repository),
    file_type: str = Query(default="md", pattern="^(md|html|pdf)$",
                           description="md 或 html；pdf 请下载 html 后浏览器打印（D-07）"),
) -> Response:
    """导出报告产物（FR-61）。

    - `md`：Markdown 原文，附件下载
    - `html`：自包含 HTML（样式内联），**浏览器打开后打印即可另存为 PDF**
    - `pdf`：**不支持服务端渲染**（D-07），返回 422 并说明替代路径
    """
    filename, content, mime = await _export(repo, report_id,
                                            file_type=file_type, actor=_actor)
    disposition = "inline" if file_type == "html" else "attachment"
    return Response(
        content=content.encode("utf-8"),
        media_type=mime,
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{_quote(filename)}",
            # 提示前端：PDF 请用浏览器打印（前端 T-32 依赖这个头决定按钮行为）
            "X-Export-Hint": "pdf-by-browser-print",
        },
    )


def _quote(filename: str) -> str:
    from urllib.parse import quote

    return quote(filename)
