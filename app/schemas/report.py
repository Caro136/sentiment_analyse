"""报告中心的请求响应模型（FR-57~FR-63）。"""

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery
from engines.contracts.enums import ReportStatus, ReportType

__all__ = [
    "ReportQuery",
    "ReportFileView",
    "ReportView",
    "ReportDetailView",
    "ReportCreateRequest",
]


class ReportQuery(PageQuery):
    """报告列表筛选（FR-57）。"""

    topic_id: str | None = Field(default=None, max_length=64)
    event_id: str | None = Field(default=None, max_length=64)
    # 报告类型是 PRD 7.3 页面上的一级分类（周期性简报 / 突发事件专项报告），
    # 前端按它分标签页——因此必须能在服务端筛，否则前端只能筛当前这一页，
    # 筛选结果看着对、实际不全
    report_type: ReportType | None = None
    status: ReportStatus | None = None


class ReportFileView(BaseModel):
    """产物文件。`file_path` 是磁盘落点，供归档核对；预览与导出不依赖它。"""

    file_type: str
    file_path: str = ""
    size_bytes: int | None = None


class ReportView(BaseModel):
    """报告摘要（列表用，**不含正文**）。"""

    report_id: str
    event_id: str | None = None
    topic_id: str | None = None
    report_type: ReportType
    type_label: str = ""
    title: str
    status: ReportStatus
    status_label: str = ""
    error_message: str | None = Field(
        default=None, description="失败原因（前端据此提示并给「重试」入口）")
    generated_at: int | None = None
    created_by: str | None = None
    created_at: int
    chars: int = Field(default=0, description="Markdown 正文字数")
    files: list[ReportFileView] = Field(default_factory=list)
    download_types: list[str] = Field(
        default_factory=list,
        description="可导出的类型：md（Markdown）/ html（自包含 HTML，可打印为 PDF）")


class ReportDetailView(ReportView):
    """报告详情与在线预览（FR-60）。"""

    content_md: str = Field(default="", description="Markdown 原文（供预览与编辑）")


class ReportCreateRequest(BaseModel):
    """发起报告生成（FR-58）。

    目标三选一：`event_id`（事件专项报告）、`topic_id`（周期简报）、
    或直接给 `query`（主题词，用于临时试跑）。`query` 同时是报告引擎用来
    加载三方产物的键——产物文件名里带的就是它。
    """

    event_id: str | None = Field(default=None, max_length=64)
    topic_id: str | None = Field(default=None, max_length=64)
    query: str | None = Field(default=None, max_length=128,
                              description="显式主题词（与事件/主题二选一）")
