"""审计上下文：操作人，以及被审计函数内部登记的前后值。

**为什么要 contextvar 而不是让每个函数都收 actor 参数**：审计要覆盖登录、配置变更、
工单流转、报告导出等一批散落各处的用例，若每条都得在签名上多带 `actor_id`，
审计就变成「谁记得传谁才留痕」——迟早有人忘。
于是操作人在鉴权依赖里写一次（`app/security.py`），此后同一请求内的所有被审计
服务调用自动带上。

asyncio 下 contextvar 按任务隔离（`create_task` 会复制当前上下文），
并发的多个请求不会串号；后台任务保留创建它的那个请求的归属，符合预期。

来源 IP 不在这里——它和 trace_id 同属「请求事实」，由 `app/observability.py` 统一
持有（审计读取它）。这样安排还避免了一个循环依赖：`app.errors` 依赖 observability，
若 observability 反过来依赖本包（其 `__init__` 会拉起 writer），导入链就成环了。

**前后值登记**：变更类操作在函数内部本来就知道旧值与新值（改之前查出来的那条记录），
让装饰器再查一遍数据库既浪费、又可能与业务判断不一致。因此提供 `note_change()`：
函数在拿到新旧值时登记一次，装饰器连同其他字段一起落库。
"""

from contextvars import ContextVar
from dataclasses import dataclass

__all__ = [
    "AuditChange",
    "begin_change",
    "end_change",
    "bind_actor",
    "current_actor",
    "reset_actor",
    "note_change",
    "note_target",
    "take_change",
    "take_target",
]

_actor: ContextVar[str | None] = ContextVar("audit_actor", default=None)
_change: ContextVar["AuditChange | None"] = ContextVar("audit_change", default=None)
_target: ContextVar[str] = ContextVar("audit_target", default="")


@dataclass(frozen=True)
class AuditChange:
    """一次变更的前后值。字段为 None 表示「无」（如新增操作没有前值）。"""

    before: object = None
    after: object = None


def bind_actor(user_id: str | None):
    """记录当前请求的操作人（由鉴权依赖调用）。返回复原用的 token。"""
    return _actor.set(user_id)


def reset_actor(token) -> None:
    """复原操作人上下文（测试与中间件收尾用）。"""
    _actor.reset(token)


def current_actor() -> str | None:
    """当前操作人的用户业务ID；不在请求上下文中时返回 None。"""
    return _actor.get()


def note_change(before: object = None, after: object = None) -> None:
    """在业务函数内登记前后值（只在被 `@audit` 装饰的函数里有效）。"""
    _change.set(AuditChange(before=before, after=after))


def note_target(target: str) -> None:
    """在业务函数内指定审计目标。

    用于**目标只有执行后才知道**的场景：例如创建报告时 `report_id` 是函数内部
    生成的，装饰器的 `target="{report_id}"` 模板渲染不出来（会原样留下占位符，
    实测踩过）。此时函数拿到 ID 后调一次 `note_target()` 即可覆盖模板。
    """
    _target.set(target)


def take_target() -> str:
    """取出函数内登记的目标（装饰器用；未登记则返回空串）。"""
    return _target.get() or ""


def take_change() -> AuditChange:
    """取出当前登记的前后值（装饰器用）。"""
    return _change.get() or AuditChange()


def begin_change():
    """开始一次登记。

    装饰器在调用业务函数**之前**调用它，返回的 token 供调用结束后复原：
    否则上一次调用登记的前后值会残留到下一次（同一个请求里连续两次被审计的
    调用就会带上别人的变更记录）。
    """
    return _change.set(None)


def end_change(token) -> None:
    """复原登记（装饰器在业务函数返回/抛出后调用）。"""
    _change.reset(token)
