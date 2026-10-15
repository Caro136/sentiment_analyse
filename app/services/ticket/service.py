"""处置工单用例（FR-49~FR-51）。

三件事在这里收口：

1. **状态机 + 流转记录同事务写**。只改状态不记轨迹，处置过程就丢了（FR-50）；
   轨迹由 `ticket_log` 承载，工单表只存当前状态。
2. **关闭动作按角色分权**：`resolved` / `ignored` 走 `ticket:close`（**仅公关**）；
   其余流转分析师与公关共享（PRD 第 2 章）。
3. **草案分三步**：生成（不落库，避免把一次探索变成正式记录）→ 编辑 →
   采纳（写进工单 + 记 `draft_adopt` 轨迹）。模型输出可能包含不实承诺，
   必须有人过一遍再采用。
"""

import time

from loguru import logger

from app.errors import conflict, invalid_request, not_found
from app.services.audit import audit, note_change
from app.services.content.repository import ContentRepository
from app.services.event.repository import EventRepository
from app.services.ticket import draft as draft_module
from app.services.ticket import lifecycle
from app.services.ticket.repository import (
    TicketLogRecord,
    TicketRecord,
    TicketRepository,
    new_ticket_id,
    snapshot,
)
from engines.contracts.enums import (
    AuditAction,
    RiskLevel,
    TicketAction,
    TicketStatus,
)

__all__ = [
    "list_tickets",
    "get_ticket_detail",
    "create_ticket",
    "update_ticket",
    "generate_response_draft",
    "adopt_response_draft",
    "to_ticket_view",
]


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

def _log_view(record: TicketLogRecord) -> dict:
    try:
        action_label = TicketAction(record.action).label
    except ValueError:
        action_label = record.action
    return {
        "action": record.action,
        "action_label": action_label,
        "from_status": record.from_status,
        "from_status_label": _status_label(record.from_status),
        "to_status": record.to_status,
        "to_status_label": _status_label(record.to_status),
        "operator_id": record.operator_id,
        "note": record.note,
        "operated_at": record.operated_at,
    }


def _status_label(status: str | None) -> str:
    if not status:
        return ""
    try:
        return TicketStatus(status).label
    except ValueError:
        return status


def to_ticket_view(record: TicketRecord, *, logs: list[TicketLogRecord] | None = None) -> dict:
    """工单视图。`logs` 传入时才带处置轨迹（列表接口不带，避免 N+1）。"""
    view = {
        "ticket_id": record.ticket_id,
        "event_id": record.event_id,
        "alert_id": record.alert_id,
        "status": record.status,
        "status_label": _status_label(record.status),
        "assignee_id": record.assignee_id,
        "risk_level": record.risk_level,
        "has_response_draft": bool(record.response_draft),
        "created_at": record.created_at,
        "closed_at": record.closed_at,
        "is_closed": TicketStatus(record.status).is_terminal,
        "allowed_transitions": sorted(
            status.value for status in
            lifecycle.ALLOWED_TRANSITIONS[TicketStatus(record.status)]),
    }
    if logs is not None:
        view["response_draft"] = record.response_draft or ""
        view["logs"] = [_log_view(log) for log in logs]
    return view


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

async def list_tickets(repo: TicketRepository, *, page: int = 1, page_size: int = 20,
                       status: str | None = None, event_id: str | None = None,
                       assignee_id: str | None = None) -> tuple[int, list[dict]]:
    total, rows = await repo.list_tickets(page=page, page_size=page_size, status=status,
                                         event_id=event_id, assignee_id=assignee_id)
    return total, [to_ticket_view(row) for row in rows]


async def get_ticket_detail(repo: TicketRepository, ticket_id: str) -> dict:
    """工单详情：含处置轨迹（FR-50 的「处置轨迹」按操作时间正序）。"""
    record = await repo.get_ticket(ticket_id)
    if record is None:
        raise not_found(f"工单不存在：{ticket_id}", detail=f"ticket_id={ticket_id}")
    logs = await repo.list_logs(ticket_id)
    return to_ticket_view(record, logs=logs)


# ---------------------------------------------------------------------------
# 创建与流转
# ---------------------------------------------------------------------------

@audit(AuditAction.TICKET_FLOW, target="ticket_event:{event_id}")
async def create_ticket(repo: TicketRepository, event_repo: EventRepository,
                        content_repo: ContentRepository, session, *,
                        actor: object, event_id: str, alert_id: str | None = None,
                        risk_level: str | None = None, assignee_id: str | None = None,
                        note: str = "") -> dict:
    """为事件开一张处置工单（FR-49/50）。

    **同一事件只允许一张未关闭的工单**：多张并行会让「这张事件处理到哪一步了」
    变成需要交叉比对的问题，而处置过程最忌含糊。已关闭的工单不阻塞新开单
    （重新处置是正常需求）。

    `risk_level` 留空时取事件的**生效风险**（人工复核过就是人工值，否则算法值）——
    不能直接读 `event.risk_level` 列：那一列在本项目里是空的（统计与风险都是现算的，
    见 `services/event`），读它只会得到 NULL。
    """
    event = await event_repo.get_event(event_id)
    if event is None:
        raise not_found(f"事件不存在：{event_id}", detail=f"event_id={event_id}")

    resolved_risk = (risk_level or "").strip()
    if not resolved_risk:
        from app.services.event import service as event_service

        summary = await event_service.get_event_detail(event_repo, content_repo, session,
                                                      event_id)
        resolved_risk = summary.get("risk_level") or ""

    existing = await repo.find_open_by_event(event_id)
    if existing is not None:
        raise conflict(
            f"该事件已有未关闭的工单（{existing.status}）：{existing.ticket_id}",
            detail=f"ticket_id={existing.ticket_id}")

    created = await repo.create_ticket(TicketRecord(
        ticket_id=new_ticket_id(),
        event_id=event_id,
        alert_id=alert_id,
        status=TicketStatus.PENDING.value,
        assignee_id=(assignee_id or "").strip() or None,
        risk_level=resolved_risk or None,
        response_draft=None,
        created_at=int(time.time()),
        closed_at=None,
    ))

    await repo.add_log(created.ticket_id, TicketLogRecord(
        action=TicketAction.CREATE.value, from_status=None,
        to_status=TicketStatus.PENDING.value,
        operator_id=getattr(actor, "user_id", ""),
        note=(note or "").strip() or "创建工单",
        operated_at=int(time.time())))

    logs = await repo.list_logs(created.ticket_id)
    note_change(after=snapshot(created))
    logger.info("新建工单 ticket_id={} event_id={} 操作人={}",
                created.ticket_id, event_id, getattr(actor, "user_id", "-"))
    return to_ticket_view(created, logs=logs)


@audit(AuditAction.TICKET_FLOW, target="ticket:{ticket_id}")
async def update_ticket(repo: TicketRepository, *, actor: object, ticket_id: str,
                        status: str | None = None, assignee_id: str | None = None,
                        note: str = "", risk_level: str | None = None) -> dict:
    """状态流转 / 认领 / 追加批注（FR-49/50）。

    - 只传 `note` 不传 `status` → 记一条 `comment` 轨迹（批注本身也是处置动作）；
    - 传 `status` 走状态机校验，**关闭动作要求 `ticket:close` 权限**（在路由层用
      不同的权限依赖表达，服务层再兜一次显式检查，避免将来有人换错依赖）；
    - 重复置为同一状态不写库，但仍留痕（与告警、风险复核同一套语义）。
    """
    current = await repo.get_ticket(ticket_id)
    if current is None:
        raise not_found(f"工单不存在：{ticket_id}", detail=f"ticket_id={ticket_id}")

    actor_id = getattr(actor, "user_id", "")
    now = int(time.time())
    before = snapshot(current)

    # 批注：不动状态，只追加轨迹
    if not status and assignee_id is None and risk_level is None:
        if not (note or "").strip():
            raise invalid_request("批注内容不能为空")
        await repo.add_log(ticket_id, TicketLogRecord(
            action=TicketAction.COMMENT.value, from_status=current.status,
            to_status=current.status, operator_id=actor_id,
            note=note.strip(), operated_at=now))
        updated = await repo.get_ticket(ticket_id) or current
        note_change(after=snapshot(updated))
        return to_ticket_view(updated, logs=await repo.list_logs(ticket_id))

    if status is not None and not lifecycle.can_transition(current.status, status):
        raise conflict(lifecycle.transition_error(current.status, status),
                       detail=f"from={current.status} to={status}")

    if status is not None and lifecycle.is_closing(status):
        if getattr(actor, "role", "") not in ("pr",):
            # 路由层已按 ticket:close 拦过，这里是第二道：服务层不依赖调用方自觉
            from app.errors import forbidden

            raise forbidden("ticket:close", detail="工单关闭仅公关运营可执行")

    fields: dict = {}
    if status is not None and status != current.status:
        fields["status"] = status
        if TicketStatus(status).is_terminal:
            fields["closed_at"] = now
    if assignee_id is not None:
        fields["assignee_id"] = assignee_id.strip() or None
    if risk_level is not None:
        fields["risk_level"] = RiskLevel(risk_level).value

    if not fields and status == current.status:
        # 重复置为同一状态：不写库，审计标为未变更
        note_change(before=before, after={**before, "changed": False})
        return to_ticket_view(current, logs=await repo.list_logs(ticket_id))

    if not fields and not (note or "").strip():
        raise invalid_request("没有需要更新的字段")

    if fields:
        updated = await repo.update_ticket(ticket_id, fields)
        if updated is None:
            raise not_found(f"工单不存在：{ticket_id}")
    else:
        updated = current

    # 状态变化或认领写 status_change/claim 轨迹；两者都带批注（如有）
    if "status" in fields and fields["status"] != current.status:
        # 转入「跟踪中」且此前无人承办 → 这是认领动作（轨迹里区分开，
        # 前端在处置轨迹上显示「认领」比「状态流转」更贴合事实）
        is_claim = (fields["status"] == TicketStatus.TRACKING.value
                    and not current.assignee_id)
        await repo.add_log(ticket_id, TicketLogRecord(
            action=(TicketAction.CLAIM.value if is_claim
                    else TicketAction.STATUS_CHANGE.value),
            from_status=current.status, to_status=fields["status"],
            operator_id=actor_id, note=(note or "").strip(), operated_at=now))
    elif "status" not in fields:
        # 仅改承办人或风险等级：记一条批注型轨迹，说明改了什么
        await repo.add_log(ticket_id, TicketLogRecord(
            action=(TicketAction.CLAIM.value if "assignee_id" in fields
                    else TicketAction.COMMENT.value),
            from_status=current.status, to_status=current.status,
            operator_id=actor_id,
            note=(note or "").strip() or _describe_change(fields), operated_at=now))

    note_change(before=before, after=snapshot(updated))
    logger.info("工单更新 ticket_id={} 字段={} 操作人={}",
                ticket_id, sorted(fields) or ["note"], actor_id or "-")
    return to_ticket_view(updated, logs=await repo.list_logs(ticket_id))


def _describe_change(fields: dict) -> str:
    if "risk_level" in fields:
        return f"风险等级调整为 {fields['risk_level']}"
    if "assignee_id" in fields:
        return f"承办人调整为 {fields['assignee_id'] or '（清空）'}"
    return "更新工单"


# ---------------------------------------------------------------------------
# 回应草案（FR-51）
# ---------------------------------------------------------------------------

async def generate_response_draft(event_repo: EventRepository,
                                  content_repo: ContentRepository, session, *,
                                  event_id: str, cluster_labels: list[str] | None = None,
                                  client=None) -> dict:
    """生成公关回应草案（**不落库**，因此不需要工单仓储）。

    不落库是刻意的：生成是探索动作，可能被反复试；落库的是「采纳」的那一份。
    这样工单上的草案永远是人工确认过的版本。
    """
    context = await draft_module.build_context(
        event_repo, content_repo, session, event_id, cluster_labels=cluster_labels)
    # 没有材料就拒绝生成：一份「看起来很正式」的空话草稿极可能被直接采用
    draft_module.ensure_generatable(context)
    draft = await draft_module.generate_draft(context, client=client)

    return {
        "event_id": event_id,
        "title": context.title,
        "risk_level": context.risk_level,
        "evidence": {
            "total_volume": context.total_volume,
            "negative_ratio": context.negative_ratio,
            "sentiment_distribution": context.sentiment_distribution,
            "cluster_labels": context.cluster_labels,
            "high_risk_quotes": context.high_risk_quotes,
            "official_statements": context.official_statements,
        },
        "draft": {
            "fact_clarification": draft.fact_clarification,
            "emotion_guidance": draft.emotion_guidance,
            "action_plan": draft.action_plan,
            "caution": draft.caution,
        },
        "rendered": draft.render(),
    }


@audit(AuditAction.DRAFT_ADOPT, target="ticket:{ticket_id}")
async def adopt_response_draft(repo: TicketRepository, *, actor: object, ticket_id: str,
                               content: str | None = None, note: str = "") -> dict:
    """采纳回应草案：写入工单并记 `draft_adopt` 轨迹（FR-51）。

    `content` 传的是**人工编辑后的最终文本**（省略则沿用已生成的内容）。
    采纳是决策动作，因此进审计——「谁在什么时候决定了对外怎么说」必须可查。
    """
    current = await repo.get_ticket(ticket_id)
    if current is None:
        raise not_found(f"工单不存在：{ticket_id}", detail=f"ticket_id={ticket_id}")

    final_text = (content or current.response_draft or "").strip()
    if not final_text:
        raise invalid_request("没有可采纳的草案内容，请先生成草案")

    actor_id = getattr(actor, "user_id", "")
    updated = await repo.update_ticket(ticket_id, {"response_draft": final_text})
    if updated is None:
        raise not_found(f"工单不存在：{ticket_id}")

    await repo.add_log(ticket_id, TicketLogRecord(
        action=TicketAction.DRAFT_ADOPT.value, from_status=current.status,
        to_status=current.status, operator_id=actor_id,
        note=(note or "").strip() or f"采纳回应草案（{len(final_text)} 字）",
        operated_at=int(time.time())))

    note_change(before=snapshot(current), after=snapshot(updated))
    logger.info("采纳回应草案 ticket_id={} 操作人={} 字数={}",
                ticket_id, actor_id or "-", len(final_text))
    return to_ticket_view(updated, logs=await repo.list_logs(ticket_id))
