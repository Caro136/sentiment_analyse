"""研判路由（API-22/23，FR-64~66）。

| 接口 | 语义 | 权限 |
|------|------|------|
| `POST /api/v1/research` | 发起多智能体研判（异步，立即 202） | `research:run`（分析师 / 管理员） |
| `GET  /api/v1/research/{task_id}` | 任务状态与各角色结果 | `research:read`（三方） |

**任务状态在进程内**（编排器的单例）——因此与事件总线、主持人会话一样，
必须单 worker 运行（ADR-05）。进程重启后旧任务查不到，这与报告不同：
报告有库表（`report`），研判的结果本来就是磁盘上的三方产物，没有中间态需要延续。

**进度不在这里返回**：它经 SSE（`role_progress`）推给前端（FR-69），
本接口只回答「跑完了没有、各角色成没成」。
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.errors import conflict, invalid_request
from app.schemas.research import (
    RESEARCH_ROLES,
    ResearchStartRequest,
    ResearchStartView,
    ResearchTaskView,
)
from app.security import require_permission
from app.services.auth.repository import UserRecord
from app.services.research import service
from engines.contracts.permissions import Permission

router = APIRouter(prefix="/api/v1/research", tags=["research"])

_RunResearch = Annotated[UserRecord, Depends(require_permission(Permission.RESEARCH_RUN))]
_ReadResearch = Annotated[UserRecord, Depends(require_permission(Permission.RESEARCH_READ))]


@router.post("", response_model=ResearchStartView, status_code=202, summary="发起多智能体研判")
async def start_research(payload: ResearchStartRequest,
                         actor: _RunResearch) -> ResearchStartView:
    """发起一次研判（FR-64）。

    - 目标二选一：`query`（主题词）或 `topic_id`（取主题名称）；
    - **同时只允许一个任务**：已有任务在跑时返回 409（主持人会话是进程内共享的，
      两个主题并发会让同名维度被错误配对）；
    - 进度经 SSE 推送（`role_progress`，角色 `insight` / `media` / `host`）。
    """
    roles = payload.roles or list(RESEARCH_ROLES)
    unknown = [role for role in roles if role not in RESEARCH_ROLES]
    if unknown:
        # 角色名写错不该静默降级为「跑全部」：那会让人以为换了个角色在跑
        raise invalid_request(f"不支持的分析角色：{'、'.join(unknown)}",
                              detail=f"可选：{'/'.join(RESEARCH_ROLES)}")

    try:
        result = await service.start_research(
            query=payload.query, topic_id=payload.topic_id, roles=roles, actor=actor)
    except service.ResearchRunning as exc:
        raise conflict(f"已有研判任务在执行（{exc.task_id}），请等待完成后再发起",
                       detail=f"task_id={exc.task_id}") from exc
    return ResearchStartView(**result)


@router.get("/{task_id}", response_model=ResearchTaskView, summary="研判任务状态与结果")
async def get_research_task(task_id: str, _actor: _ReadResearch) -> ResearchTaskView:
    """任务状态与各角色结果（FR-66）。`results` 里每个角色带产物路径与摘要。"""
    return ResearchTaskView(**service.get_task(task_id))
