"""系统运维的请求响应模型（FR-72~FR-75）。"""

from pydantic import BaseModel, Field

from app.schemas.common import PageQuery

__all__ = [
    "TaskQuery",
    "TaskView",
    "TaskSummaryItem",
    "TaskSummaryView",
    "LogModuleView",
    "LogLineView",
    "LogSearchView",
    "ResetRequest",
    "ResetPlanView",
]


class TaskQuery(PageQuery):
    """任务看板筛选（FR-72）。"""

    task_type: str | None = Field(default=None, max_length=32,
                                  description="collect/analyze/research/report/alert/dashboard")
    status: str | None = Field(default=None, max_length=16,
                               description="running/success/failed")
    start_time: int | None = Field(default=None, description="开始时间起（Unix 时间戳）")
    end_time: int | None = Field(default=None, description="开始时间止")


class TaskView(BaseModel):
    """一条任务运行记录。"""

    task_id: str
    task_type: str
    status: str
    started_at: int
    finished_at: int | None = None
    duration_ms: int | None = None
    input_summary: str = ""
    error_message: str | None = None
    running_seconds: int | None = Field(
        default=None, description="仍在运行时的已耗时；已终结为 null")


class TaskSummaryItem(BaseModel):
    """按类型的任务汇总。`avg_duration_ms` 不含仍在运行的任务。"""

    task_type: str
    total: int = 0
    success: int = 0
    failed: int = 0
    running: int = 0
    avg_duration_ms: float | None = None


class TaskSummaryView(BaseModel):
    items: list[TaskSummaryItem] = Field(default_factory=list)
    counts_by_status: dict[str, int] = Field(default_factory=dict)


class LogModuleView(BaseModel):
    """可检索的日志模块（按角色分流，见 `common/logging.py`）。"""

    module: str
    exists: bool = False
    size_bytes: int = 0


class LogLineView(BaseModel):
    module: str
    timestamp: str = ""
    level: str = ""
    text: str = Field(description="日志原文（读取时统一脱敏）")


class LogSearchView(BaseModel):
    """日志检索结果（FR-73）。

    `truncated=true` 表示命中数超过 limit，**只返回最近 limit 行**——
    日志检索没有 offset 分页：查日志的诉求是「最近发生了什么」，
    往前翻页的价值远低于把时间范围收紧。
    """

    module: str
    matched: int = 0
    scanned: int = Field(default=0, description="回读扫描的行数（用于判断是否需要收紧条件）")
    truncated: bool = False
    lines: list[LogLineView] = Field(default_factory=list)


class ResetRequest(BaseModel):
    """演示数据重置（FR-75）。**不可撤销**，需原样填写确认口令。"""

    scope: str = Field(description="analysis / reports / alerts / runtime / all")
    confirm: str = Field(description="确认口令，如 RESET_ANALYSIS（见 preview 响应）")
    dry_run: bool = Field(default=False, description="只统计影响面，不删除")


class ResetPlanView(BaseModel):
    scope: str
    tables: list[str] = Field(default_factory=list)
    row_counts: dict[str, int] = Field(default_factory=dict, description="各表将被删除的行数")
    deleted: dict[str, int] = Field(default_factory=dict, description="实际删除的行数（dry_run 时为空）")
    total_rows: int = 0
    protected_tables: dict[str, str] = Field(
        default_factory=dict, description="永不重置的表及原因")
