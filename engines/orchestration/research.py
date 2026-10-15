"""研究任务编排。

一次研判任务的完整生命周期：

    启动主持人监听 → 并发启动分析角色 → 角色逐章产出并发布事件
                                            ↓
                            主持人按维度配对研判（事件驱动，不在这里等）
                                            ↓
                              任务完成时发布结果事件

**顺序不能反**：主持人必须先于分析角色启动。反过来的话，
角色在最开始几秒产出的章节事件会因为没有订阅者而丢失——
表现为「主持人永远差一个维度，最终裁判出不来」，而且不报任何错。

**编排层不关心角色内部流程**：它只负责按注册表把角色跑起来、
把进度与结果转成事件发出去。角色内部是 LangGraph 八节点还是五节点，
编排层不知道也不需要知道。
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from engines.common.eventing.publishers import (
    publish_role_error,
    publish_role_progress,
    publish_role_result,
)
from engines.common.logging import logger, mask_secret
from engines.contracts.roles import get_role_info
from engines.host_agent import HostAgent
from engines.orchestration.registry import ResearchRole, resolve_roles

# 任务状态
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class RoleOutcome:
    """单个角色的执行结果。"""

    role: str
    display_name: str = ""
    ok: bool = False
    duration: float = 0.0
    report_path: str = ""
    summary: str = ""
    error: str = ""


@dataclass
class ResearchTask:
    """一次研判任务。"""

    task_id: str
    query: str
    status: str = STATUS_RUNNING
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    outcomes: dict[str, RoleOutcome] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        end = self.finished_at or time.time()
        return end - self.started_at

    @property
    def succeeded(self) -> int:
        return sum(1 for o in self.outcomes.values() if o.ok)

    @property
    def failed_roles(self) -> list[str]:
        return [r for r, o in self.outcomes.items() if not o.ok]

    def render(self) -> str:
        parts = [f"任务 {self.task_id}（{self.query}）{self.status}，"
                 f"耗时 {self.duration:.0f}s"]
        for outcome in self.outcomes.values():
            mark = "✓" if outcome.ok else "✗"
            detail = outcome.error or outcome.summary or outcome.report_path
            parts.append(f"  {mark} {outcome.display_name}：{detail}")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        """接口返回用的结构。"""
        return {
            "task_id": self.task_id,
            "query": self.query,
            "status": self.status,
            "duration": round(self.duration, 1),
            "results": {
                role: {
                    "display_name": outcome.display_name,
                    "ok": outcome.ok,
                    "report_path": outcome.report_path,
                    "summary": outcome.summary,
                    "error": outcome.error,
                    "duration": round(outcome.duration, 1),
                }
                for role, outcome in self.outcomes.items()
            },
        }


class ResearchOrchestrator:
    """研判任务编排器（进程内单例使用）。

    同时只允许一个研判任务在跑：主持人的会话是共享的，
    两个主题并发会让章节配对互相污染（PairStore 按 section_key 配对，
    两个主题的同名维度会被错误地凑成一对）。
    """

    def __init__(self) -> None:
        self._host = HostAgent()
        self._tasks: dict[str, ResearchTask] = {}
        self._current: ResearchTask | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------

    @property
    def host(self) -> HostAgent:
        return self._host

    @property
    def current(self) -> ResearchTask | None:
        return self._current

    def get_task(self, task_id: str) -> ResearchTask | None:
        return self._tasks.get(task_id)

    def latest_task(self) -> ResearchTask | None:
        if not self._tasks:
            return None
        return max(self._tasks.values(), key=lambda t: t.started_at)

    # ------------------------------------------------------------------

    async def start(self, query: str, *,
                    roles: list[str] | None = None) -> ResearchTask:
        """启动一次研判任务。返回任务对象（不等任务跑完）。

        调用方拿到 task_id 后应立即返回 202——任务在后台跑，
        进度通过 SSE 推送（FR-69）。
        """
        async with self._lock:
            if self._current is not None and self._current.status == STATUS_RUNNING:
                raise RuntimeError(
                    f"已有研判任务在执行（{self._current.task_id}），"
                    f"请先停止或等待完成")

            task = ResearchTask(task_id=f"res_{uuid.uuid4().hex[:12]}", query=query)
            self._tasks[task.task_id] = task
            self._current = task

            # 主持人**先启动**：晚于角色启动会丢掉最早的几章事件
            self._host.start(query)
            logger.info("研判任务 {} 启动：{}（角色 {}）",
                        task.task_id, query, roles or "全部")

            asyncio.create_task(self._run(task, roles),
                                name=f"research-{task.task_id}")
            return task

    async def _run(self, task: ResearchTask, roles: list[str] | None) -> None:
        try:
            resolved = resolve_roles(roles)
        except KeyError as exc:
            task.status = STATUS_FAILED
            task.finished_at = time.time()
            logger.error("研判任务 {} 角色解析失败：{}", task.task_id, exc)
            return

        async def run_role(role: ResearchRole) -> RoleOutcome:
            info = get_role_info(role.key)
            started = time.monotonic()
            publish_role_progress(role.key, "planning", f"{info.display_name}启动", 5)
            try:
                result = await role.invoke(task.query)
            except Exception as exc:  # noqa: BLE001
                error = mask_secret(f"{type(exc).__name__}: {exc}")[:200]
                publish_role_error(role.key, error)
                logger.exception("角色 {} 执行异常", role.key)
                return RoleOutcome(role=role.key, display_name=info.display_name,
                                   ok=False, duration=time.monotonic() - started,
                                   error=error)

            outcome = RoleOutcome(
                role=role.key,
                display_name=info.display_name,
                ok=bool(getattr(result, "ok", False)),
                duration=time.monotonic() - started,
                report_path=getattr(result, "report_path", "") or "",
                summary=getattr(result, "render", lambda: "")() or "",
                error=getattr(result, "error", "") or "",
            )
            if outcome.ok:
                publish_role_result(role.key, outcome.report_path)
            else:
                publish_role_error(role.key, outcome.error or "角色未产出有效结果")
            return outcome

        # 两个研究员并发出题；各自的章节事件会驱动主持人并行研判
        outcomes = await asyncio.gather(*(run_role(r) for r in resolved))
        for outcome in outcomes:
            task.outcomes[outcome.role] = outcome

        task.finished_at = time.time()
        task.status = STATUS_COMPLETED if task.failed_roles == [] else STATUS_FAILED
        logger.info("研判任务 {} 结束：\n{}", task.task_id, task.render())

    # ------------------------------------------------------------------

    async def stop(self) -> None:
        """停止主持人并结束当前任务。"""
        await self._host.stop()
        if self._current and self._current.status == STATUS_RUNNING:
            self._current.status = STATUS_FAILED
            self._current.finished_at = time.time()
        self._current = None

    def discussion(self) -> list[dict]:
        """主持人的讨论记录（供前端轮询）。"""
        return self._host.discussion_records()

    def status(self) -> dict:
        return {
            "host": self._host.status().__dict__,
            "current_task": self._current.to_dict() if self._current else None,
        }

    def clear_finished(self) -> int:
        """清理已结束的任务记录。"""
        finished = [tid for tid, t in self._tasks.items()
                    if t.status != STATUS_RUNNING and t is not self._current]
        for task_id in finished:
            self._tasks.pop(task_id, None)
        return len(finished)


# 进程内单例
_orchestrator: ResearchOrchestrator | None = None


def get_orchestrator() -> ResearchOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ResearchOrchestrator()
    return _orchestrator


def reset_orchestrator() -> None:
    global _orchestrator
    _orchestrator = None


# 供类型标注引用的别名
RoleInvoker = Callable[[str], Awaitable[Any]]
