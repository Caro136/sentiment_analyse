"""`@audit` 装饰器：把关键操作自动留痕（PRD FR-04，设计规格 8.1）。

用法（`target` 支持 `{参数名}` 模板）：

```python
@audit(AuditAction.USER_UPDATE, target="user:{user_id}")
async def update_user(repo, *, actor_id, user_id, ...):
    ...
    note_change(before=old, after=new)      # 可选：登记前后值
    return record
```

三个设计要点：

1. **操作人优先从上下文取，同时兼容显式传入**。HTTP 请求由鉴权依赖写上下文；
   脚本 / 后台任务 / 服务层直调时上下文里没有，装饰器会从 `actor` / `actor_id`
   参数补上（见 `_actor_id_of`）——否则那些路径的审计会留下 `user_id=NULL`。
2. **失败也留痕，且不吞异常**。业务异常先写审计再原样抛出：登录口令错误、
   越权尝试这类「没成功的操作」恰恰是审计的核心价值。成功/失败写进 `after_value`
   （审计表没有结果列，DDL 已冻结）。
3. **前后值由业务函数登记**（`note_change`），装饰器不重查数据库：函数内部
   拿到的旧值就是它做判断用的那份，重查一遍可能不一致。

装饰器只支持 `async def`——本项目服务层全是异步的，同步函数走这条路要么是误用，
要么说明它不该被审计（纯函数没有留痕价值）。
"""

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from app.services.audit import writer
from app.services.audit.context import (
    begin_change,
    bind_actor,
    current_actor,
    end_change,
    note_change,
    note_target,
    reset_actor,
    take_change,
    take_target,
)
from engines.contracts.enums import AuditAction

__all__ = ["audit", "note_change"]


def _render_target(template: str, values: dict[str, Any]) -> str:
    """把 `user:{user_id}` 渲染成 `user:u123`。

    模板里引用不到的键不报错，只保留占位原文——审计目标写不清楚是小事，
    因为一个拼错的占位符让业务操作失败是大事。
    """
    if not template:
        return ""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        logger.warning("审计 target 模板渲染失败：{}", template)
        return template


def _bound_arguments(signature: inspect.Signature, args: tuple, kwargs: dict) -> dict[str, Any]:
    """把实参映射成「形参名 → 值」。

    调用点写 `service.login(repo, username, password)` 用的是位置参数，
    只认 `kwargs` 的话 `target="username:{username}"` 就渲染不出来。
    用 `bind_partial`（而非 `bind`）是为了容忍缺参——参数不全时按缺省处理，
    留给真正的业务函数去抛 TypeError。
    """
    try:
        return dict(signature.bind_partial(*args, **kwargs).arguments)
    except TypeError:
        return dict(kwargs)


def _actor_id_of(values: dict[str, Any]) -> str | None:
    """从调用实参里取操作人。

    支持两种约定：`actor=<用户对象>`（服务层的写法，取其 `user_id`）与
    `actor_id="u1"`。

    **为什么装饰器要管这件事**：操作人平时由鉴权依赖写进上下文，
    但服务层并非只被 HTTP 调用——脚本、后台任务、事件监听器直接调它时，
    上下文里没有操作人，审计就会写成 `user_id=NULL`。
    那种记录事后无法回答「谁做的」，而审计的全部意义就在于此。
    """
    actor = values.get("actor")
    if actor is not None:
        actor_id = getattr(actor, "user_id", None)
        if actor_id:
            return str(actor_id)
    actor_id = values.get("actor_id")
    return str(actor_id) if actor_id else None


def audit(action: AuditAction | str, *, target: str = "") -> Callable:
    """装饰一个服务层协程函数，成功与失败都写 `audit_log`。

    `action` 必须是 `AuditAction` 的成员（DDL 注释里声明的取值集合），
    传字符串也会被校验——写错动作名会让审计查询永远筛不到，
    而这类错误不会有任何异常表现。
    """
    try:
        action_value = AuditAction(action).value
    except ValueError:
        raise ValueError(
            f"未知审计动作 {action!r}，可选："
            + "/".join(a.value for a in AuditAction)
        ) from None

    def _decorate(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if not inspect.iscoroutinefunction(func):
            raise TypeError(f"@audit 只能装饰 async 函数：{func.__qualname__}")

        signature = inspect.signature(func)

        @functools.wraps(func)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = _bound_arguments(signature, args, kwargs)
            rendered = _render_target(target, bound)
            token = begin_change()

            # 上下文里没有操作人（非 HTTP 调用）时，用显式传入的 actor 补上
            actor_token = None
            explicit_actor = _actor_id_of(bound)
            if explicit_actor and not current_actor():
                actor_token = bind_actor(explicit_actor)

            error: BaseException | None = None
            try:
                return await func(*args, **kwargs)
            except BaseException as exc:  # 含 CancelledError：留痕后原样抛出
                error = exc
                raise
            finally:
                change = take_change()
                end_change(token)
                # 函数内登记的目标优先：目标可能只有执行后才知道（如新建报告的 ID），
                # 模板渲染不出来时会原样留下 `{report_id}` 这种占位符
                await writer.write_entry(
                    action_value,
                    target=(take_target() or rendered),
                    change=change,
                    ok=error is None,
                    error=error,
                )
                if actor_token is not None:
                    reset_actor(actor_token)

        return _wrapper

    return _decorate
