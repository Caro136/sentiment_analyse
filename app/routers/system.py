"""系统运维路由（FR-72~FR-75）。

| 接口 | 语义 | 权限 |
|------|------|------|
| `GET /system/tasks` | 任务执行状态看板（FR-72） | `system:read`（管理员） |
| `GET /system/tasks/summary` | 按类型的汇总（次数/成功率/平均耗时） | `system:read` |
| `GET /system/logs` | 运行日志检索（FR-73） | `system:read` |
| `POST /system/reset` | 演示数据重置（FR-75，**需确认口令**） | `system:read` + 口令 |

为什么这些接口都归管理员：PRD 第 2 章把「查看系统运行日志与任务状态」列为管理员职责，
而重置是运维动作。**重置没有单独的权限点**，而是用「管理员 + 确认口令」双重约束——
新增权限点会让矩阵膨胀，而真正防误操作的是口令（见 `services/system/reset.py`）。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.common import MAX_PAGE_SIZE, PageResult, paginate
from app.schemas.system import (
    LogModuleView,
    LogSearchView,
    ResetPlanView,
    ResetRequest,
    TaskQuery,
    TaskSummaryView,
    TaskView,
)
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.system import logs as log_service
from app.services.system import reset as reset_service
from app.services.system.reset import SqlResetExecutor
from app.services.system.tasks import TASK_TYPES, SqlTaskRepository, TaskRecord
from engines.common.db.session import get_db_session
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/system", tags=["system"])

_SystemAdmin = Annotated[UserRecord, Depends(require_permission(Permission.SYSTEM_READ))]


async def get_task_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlTaskRepository:
    return SqlTaskRepository(session)


async def get_reset_executor(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SqlResetExecutor:
    """重置执行器依赖。测试用 `app.dependency_overrides` 换成替身——
    这是最危险的入口，它必须能在离线测试里被覆盖。"""
    return SqlResetExecutor(session)


def _to_task_view(record: TaskRecord) -> TaskView:
    return TaskView(
        task_id=record.task_id, task_type=record.task_type, status=record.status,
        started_at=record.started_at, finished_at=record.finished_at,
        duration_ms=record.duration_ms, input_summary=record.input_summary,
        error_message=record.error_message, running_seconds=record.running_seconds)


@router.get("/tasks", response_model=PageResult[TaskView], summary="任务执行状态看板")
async def list_tasks(
    _actor: _SystemAdmin,
    repo=Depends(get_task_repository),
    task_type: str | None = Query(default=None,
                                  description="任务类型：" + "/".join(TASK_TYPES)),
    status: str | None = Query(default=None, description="running/success/failed"),
    start_time: int | None = Query(default=None),
    end_time: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
) -> PageResult[TaskView]:
    """任务运行状态与耗时（FR-72），按开始时间倒序。

    仍在运行的任务带 `running_seconds`（已耗时）；已终结的带 `duration_ms`。
    """
    query = TaskQuery(page=page, page_size=page_size, task_type=task_type,
                      status=status, start_time=start_time, end_time=end_time)
    total, records = await repo.list_tasks(
        page=query.page, page_size=query.page_size, task_type=query.task_type,
        status=query.status, start_time=query.start_time, end_time=query.end_time)
    return paginate(total, [_to_task_view(r) for r in records], query.page, query.page_size)


@router.get("/tasks/summary", response_model=TaskSummaryView, summary="任务汇总")
async def summary_tasks(_actor: _SystemAdmin,
                        repo=Depends(get_task_repository)) -> TaskSummaryView:
    """按类型汇总次数、成功率与平均耗时。平均耗时**不含仍在运行的任务**。"""
    items = await repo.summary_by_type()
    counts = await repo.counts_by_status()
    return TaskSummaryView(items=items, counts_by_status=counts)


@router.get("/logs/modules", response_model=list[LogModuleView], summary="可检索的日志模块")
async def list_log_modules(_actor: _SystemAdmin) -> list[LogModuleView]:
    """日志按角色分流（`logs/{module}.log`），这里给出各文件现状。"""
    return [LogModuleView(**item) for item in log_service.available_modules()]


@router.get("/logs", response_model=LogSearchView, summary="运行日志检索")
async def search_logs(
    _actor: _SystemAdmin,
    module: str = Query(default="app", description="日志模块：app 或角色标识"),
    keyword: str | None = Query(default=None, max_length=128, description="关键字（子串匹配）"),
    level: str | None = Query(default=None, max_length=16,
                             description="级别：INFO/WARNING/ERROR/DEBUG"),
    trace_id: str | None = Query(default=None, max_length=64,
                                 description="按请求链路检索（串起一次请求的全部日志）"),
    start_time: str | None = Query(default=None, max_length=19,
                                   description="起始时间，如 2026-10-15 或 2026-10-15 19:00:00"),
    end_time: str | None = Query(default=None, max_length=19),
    limit: int = Query(default=100, ge=1, le=log_service.MAX_LIMIT),
) -> LogSearchView:
    """检索日志尾部（FR-73）。

    只返回**最近的** limit 行（没有 offset 分页）：查日志的诉求是「最近发生了什么」，
    往前翻页不如收紧时间范围。返回前统一脱敏——第三方库打印的内容不受我们控制。
    """
    result = log_service.search_logs(
        module=module, keyword=keyword, level=level, trace_id=trace_id,
        start_time=start_time, end_time=end_time, limit=limit)
    return LogSearchView(
        module=result.module, matched=result.matched, scanned=result.scanned,
        truncated=result.truncated,
        lines=[{"module": line.module, "timestamp": line.timestamp,
                "level": line.level, "text": line.raw} for line in result.lines])


@router.post("/reset", response_model=ResetPlanView, summary="演示数据重置")
async def reset_data(payload: ResetRequest, actor: _SystemAdmin,
                     executor: SqlResetExecutor = Depends(get_reset_executor),
                     ) -> ResetPlanView:
    """把演示数据恢复到干净状态（FR-75），**不可撤销**。

    三条硬约束（详见 `services/system/reset.py`）：
    ① 不碰原始区（ADR-03：只读，重新生成数据集才是原始数据的「重置」）；
    ② 不删账号与审计（否则登不进来、也查不到谁执行的）；
    ③ 必须原样填写确认口令（`scope=analysis` → `RESET_ANALYSIS`），
       建议先用 `dry_run=true` 看清会删多少行。
    """
    plan = await reset_service.reset_demo_data(
        executor, scope=payload.scope, confirm=payload.confirm, actor=actor,
        dry_run=payload.dry_run)
    return ResetPlanView(**plan.to_dict())
