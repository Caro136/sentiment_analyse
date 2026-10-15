"""报告中心服务（T-27，对应 PRD FR-57~FR-63）。

| 模块 | 职责 |
|------|------|
| `repository` | `report` / `report_file` / `task_run` 读写（正文同时存库） |
| `service`    | 任务创建、后台生成、列表、预览、导出（审计在这一层） |

生成链路：`POST /reports` 落记录并起后台任务 → `report_engine` 产出五段式
Markdown/HTML → 落盘 + 写 `report_file` → 更新状态 → 发进度事件（前端进度条复用 SSE）。

**PDF 不在服务端渲染**（D-07）：导出给 Markdown 与自包含 HTML，PDF 由浏览器打印生成；
请求 `file_type=pdf` 会被明确拒绝并说明替代路径。
"""

from app.services.report.service import (
    create_report,
    get_download_payload,
    get_report_detail,
    list_reports,
    resolve_query,
    run_generation,
)

__all__ = [
    "list_reports", "get_report_detail", "create_report", "run_generation",
    "resolve_query", "get_download_payload",
]
