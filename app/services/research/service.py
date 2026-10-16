"""研判任务用例（FR-64~66）。

三件事在这里收口：

1. **研判主题词的解析**。`query` 有两个用途且必须是同一个值：① 作为三方产物
   的文件名键（`report_engine` 按它加载 `insight_*` / `media_*` / `host_*`），
   ② 报告标题与后续检索。所以给了 `topic_id` 就取主题名称，不让调用方各传各的——
   与报告服务（`services/report/service.py` 的 `resolve_query`）同一条口径。
2. **「同时只允许一个研判任务」翻译成 409**（硬约束：主持人会话是进程内共享的，
   两个主题并发会让同名维度被错误配对）。编排层抛 RuntimeError，
   这里换成 `conflict`，路由不必认识引擎的异常。
3. **发起进审计**（FR-04）：它是要花模型额度、要落产物的关键动作，
   「谁在什么时候发起了一次研判」必须可查。
"""

import time

from loguru import logger

from app.errors import conflict, invalid_request, not_found
from app.services.audit import audit, note_change
from app.services.monitoring.repository import SqlMonitoringRepository
from engines.common.db.session import session_scope
from engines.contracts.enums import AuditAction
from engines.orchestration import get_orchestrator
from engines.orchestration.research import ResearchTask

__all__ = [
    "start_research",
    "get_task",
    "resolve_query",
    "ResearchRunning",
]


class ResearchRunning(RuntimeError):
    """已有研判任务在执行（编排层的拒绝，由路由翻译成 409）。"""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"已有研判任务在执行：{task_id}")
        self.task_id = task_id


async def resolve_query(*, query: str | None, topic_id: str | None) -> str:
    """把「主题词 / 主题 ID」解析成研判主题词。"""
    if query and query.strip():
        return query.strip()
    if not topic_id:
        raise invalid_request("需要提供 query（研判主题词）或 topic_id 之一")

    async with session_scope() as session:
        topic = await SqlMonitoringRepository(session).get_topic(topic_id)
    if topic is None:
        raise not_found(f"主题不存在：{topic_id}", detail=f"topic_id={topic_id}")
    return topic.name


@audit(AuditAction.RESEARCH_RUN, target="research:{query}")
async def start_research(*, query: str, topic_id: str | None = None,
                         roles: list[str] | None = None,
                         actor: object = None) -> dict:
    """发起一次研判，**立即返回**（任务在后台跑，进度走 SSE）。

    返回 `{task_id, started, query, status, roles}`。已有任务在跑时抛 `ResearchRunning`。
    """
    resolved = await resolve_query(query=query, topic_id=topic_id)
    orchestrator = get_orchestrator()

    try:
        task: ResearchTask = await orchestrator.start(resolved, roles=roles)
    except RuntimeError as exc:
        # 编排层的「已有任务在跑」是唯一的 RuntimeError 来源，但它只给了消息文本，
        # 任务 ID 还得从当前任务上取——不猜文本格式（改文案不该改行为）
        current = orchestrator.current
        raise ResearchRunning(current.task_id if current else "未知") from exc

    note_change(after={"task_id": task.task_id, "query": resolved,
                       "topic_id": topic_id, "roles": roles, "started_at": time.time()})
    logger.info("研判任务已受理 task_id={} query={} 操作人={}",
                task.task_id, resolved, getattr(actor, "user_id", "-"))
    return {
        "task_id": task.task_id,
        "started": True,
        "query": task.query,
        "status": task.status,
        "roles": list(roles or ("insight", "media")),
    }


def get_task(task_id: str) -> dict:
    """查询研判任务状态与各角色结果（FR-66）。"""
    task = get_orchestrator().get_task(task_id)
    if task is None:
        raise not_found(f"研判任务不存在：{task_id}", detail=f"task_id={task_id}")
    return task.to_dict()
