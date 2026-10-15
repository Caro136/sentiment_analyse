"""审计落库：`audit_log` 的唯一写入入口。

**用独立事务，不搭业务事务的车**：审计要记的恰恰包括「操作被业务规则拒绝」
（如管理员想停用最后一个管理员、登录口令错误）。如果审计行与业务行在同一事务里，
业务一回滚就把「有人尝试过这个动作」的记录一并抹掉了——那正是最需要留痕的场景。
因此这里自己开一个 `session_scope()`。

写入失败的处理是**刻意的取舍**：记 ERROR 日志后照常返回，不把异常抛回业务。
理由是审计属于安全可观测性，不能让「审计表不可写」升级成「平台不可用」
（配置变更、工单流转都会因此失败）。但失败必须显式可见——ERROR 级 + trace_id，
便于接告警。若将来要求「审计写不进去就拒绝执行」，把 `write_entry` 里的
try/except 去掉即可，调用点无需改动。
"""

import json
import time
from typing import Any

from loguru import logger
from sqlalchemy import select

from app.observability import current_client_ip, current_trace_id
from app.services.audit.context import AuditChange, current_actor
from engines.common.db.models import AuditLog
from engines.common.db.session import session_scope

__all__ = ["write_entry", "list_entries", "MAX_VALUE_CHARS", "serialize_value"]

# 单侧（before/after）序列化后的最大字符数。audit_log.before_value 是 text，
# 但把整份主题配置塞进去没有意义，日志检索时反而难读。
MAX_VALUE_CHARS = 2000


def serialize_value(value: Any) -> str | None:
    """把前后值转成可入库的文本。

    - `None` 保持 `None`（库里是 NULL，与「前值是空字符串」区分开）
    - 字符串原样返回，不做二次转义（读审计时看到的是人写的那句话）
    - 其余走 `json.dumps`，中文不转义；不可序列化的对象退化成 repr
      ——审计宁可记下对象的字符串形态，也不要因为一个自定义类型丢掉整条记录
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = repr(value)
    if len(text) > MAX_VALUE_CHARS:
        return text[:MAX_VALUE_CHARS] + f"…（已截断，原长 {len(text)}）"
    return text


def _describe_error(error: BaseException | None) -> str | None:
    """把失败原因压成一行错误码/异常名。**不带异常原文**——原文可能含密钥。

    用 `getattr(error, "code")` 而不是 `isinstance(error, AppError)`：
    本模块不 import `app.errors`（那会让 `errors → observability → audit` 成环），
    而「带 code 属性即业务异常」本就是 AppError 的全部协议。
    """
    if error is None:
        return None
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code
    return type(error).__name__


async def write_entry(action: str, *, target: str = "", change: AuditChange | None = None,
                      actor_id: str | None = None, ip: str | None = None,
                      ok: bool = True, error: BaseException | None = None) -> bool:
    """写一条审计记录。返回是否写入成功（失败只记日志，不抛）。"""
    change = change or AuditChange()

    # 失败时把结论写进 after_value：审计表没有单独的结果列（DDL 已冻结），
    # 直接复用后值字段，读的时候一眼能看出「这次操作没成」。
    after_value = serialize_value(change.after)
    if not ok:
        failure = {"ok": False, "error": _describe_error(error)}
        after_value = json.dumps(failure, ensure_ascii=False)

    row = AuditLog(
        user_id=actor_id if actor_id is not None else current_actor(),
        action=action,
        target=(target or "")[:255] or None,
        before_value=serialize_value(change.before),
        after_value=after_value,
        ip=(ip if ip is not None else current_client_ip() or "")[:64] or None,
        operated_at=int(time.time()),
    )

    try:
        async with session_scope() as session:
            session.add(row)
    except Exception as exc:  # noqa: BLE001 —— 审计写失败不得影响业务，见模块说明
        # 这里必须带完整回溯：只记异常类型的话，出问题时会得到一个
        # 「审计写入失败 AttributeError」这样查不下去的日志（联调时真踩过）
        logger.bind(trace_id=current_trace_id()).opt(exception=exc).error(
            "审计写入失败 action={} target={}", action, target)
        return False

    logger.bind(trace_id=current_trace_id()).info(
        "审计 action={} actor={} target={} ok={}",
        action, row.user_id or "-", target or "-", ok)
    return True


async def list_entries(*, page: int = 1, page_size: int = 20,
                       action: str | None = None, user_id: str | None = None,
                       start_time: int | None = None, end_time: int | None = None,
                       ) -> tuple[int, list[AuditLog]]:
    """按条件分页查询审计记录（FR-72~75 的日志检索由 T-28 接成接口）。

    时间倒序：审计的阅读方式几乎总是「最近发生了什么」。
    """
    from sqlalchemy import func

    conditions = []
    if action:
        conditions.append(AuditLog.action == action)
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if start_time is not None:
        conditions.append(AuditLog.operated_at >= start_time)
    if end_time is not None:
        conditions.append(AuditLog.operated_at <= end_time)

    total_stmt = select(func.count()).select_from(AuditLog)
    rows_stmt = select(AuditLog).order_by(AuditLog.operated_at.desc(), AuditLog.id.desc())
    for condition in conditions:
        total_stmt = total_stmt.where(condition)
        rows_stmt = rows_stmt.where(condition)

    async with session_scope() as session:
        total = int((await session.execute(total_stmt)).scalar() or 0)
        rows = (await session.execute(
            rows_stmt.offset((page - 1) * page_size).limit(page_size)
        )).scalars().all()
    return total, list(rows)
