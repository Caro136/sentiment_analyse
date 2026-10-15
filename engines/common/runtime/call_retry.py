"""指数退避重试。

大模型与搜索服务是外部依赖，限流、网络抖动、偶发 5xx 都是常态。这里提供两种策略：

| 策略 | 行为 | 适用 |
|------|------|------|
| `with_retry` | 重试耗尽后**抛出异常** | 关键路径：拿不到结果就无法继续，必须让上层失败并上报 |
| `with_graceful_retry` | 重试耗尽后**返回兜底值** | 尽力而为的环节：某个维度分析失败不该中断整份报告 |

**不重试的情况**：4xx（除 429 限流）。认证失败、参数错误、模型不存在这类问题
重试一百次也是一样的结果，只会白等并放大成本。
"""

import asyncio
import functools
import inspect
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from engines.common.logging import logger, mask_secret

# 常见的可重试异常类名（不直接 import 各 SDK，避免为可选依赖建立硬依赖）
_RETRYABLE_EXCEPTION_NAMES = {
    "APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
    "ReadTimeout", "TimeoutException", "InternalServerError", "ServiceUnavailableError",
    "RateLimitError", "RemoteProtocolError", "PoolTimeout",
}
# 明确不重试的异常类名
_NON_RETRYABLE_EXCEPTION_NAMES = {
    "AuthenticationError", "PermissionDeniedError", "NotFoundError",
    "BadRequestError", "UnprocessableEntityError",
}


@dataclass(frozen=True)
class RetryConfig:
    """重试参数。

    initial_delay 默认 3 秒而非更短：模型服务的限流窗口通常是秒级的，
    退避太激进会在限流时连续撞墙，反而拖长整体耗时。
    """

    max_retries: int = 4
    initial_delay: float = 3.0
    backoff_factor: float = 2.0
    max_delay: float = 45.0
    jitter: float = 0.15          # 抖动比例，避免多个并发请求同步重试形成尖峰


DEFAULT_RETRY = RetryConfig()


def delay_for(attempt: int, config: RetryConfig = DEFAULT_RETRY) -> float:
    """计算第 attempt 次重试（1 起）的等待秒数。"""
    raw = config.initial_delay * (config.backoff_factor ** (attempt - 1))
    capped = min(raw, config.max_delay)
    if config.jitter:
        capped *= 1 + random.uniform(-config.jitter, config.jitter)
    return max(0.0, capped)


def _status_code_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "http_status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_retryable(exc: BaseException) -> bool:
    """判断异常是否值得重试。

    优先看明确的黑/白名单类名，其次看 HTTP 状态码，最后乐观重试——
    「不确定能不能重试」时重试一次的成本，通常低于丢掉一次分析结果。
    """
    name = type(exc).__name__
    if name in _NON_RETRYABLE_EXCEPTION_NAMES:
        return False
    if name in _RETRYABLE_EXCEPTION_NAMES:
        return True

    status = _status_code_of(exc)
    if status is None:
        # 网络层异常（socket 层）按可重试处理
        return isinstance(exc, (ConnectionError, TimeoutError, OSError))

    if status == 429:               # 限流 → 重试
        return True
    if 400 <= status < 500:         # 其他客户端错误 → 不重试
        return False
    return True                     # 5xx → 重试


def _log_retry(operation: str, attempt: int, total: int, wait: float,
               exc: BaseException) -> None:
    logger.warning(
        "重试 {}/{} | op={} | 等待 {:.1f}s | 原因：{}",
        attempt, total, operation, wait, mask_secret(_describe(exc)),
    )


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def with_retry(func: Callable | None = None, *, config: RetryConfig | None = None,
               operation: str | None = None):
    """刚性重试装饰器：重试耗尽后抛出最后一个异常。

    同时支持同步与异步函数。

    用法：
        @with_retry(operation="generate_section")
        async def call_model(...): ...
    """
    cfg = config or DEFAULT_RETRY

    def decorator(fn: Callable) -> Callable:
        op = operation or getattr(fn, "__qualname__", str(fn))

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                last_exc: BaseException | None = None
                for attempt in range(1, cfg.max_retries + 1):
                    try:
                        return await fn(*args, **kwargs)
                    except BaseException as exc:  # noqa: BLE001
                        last_exc = exc
                        if not is_retryable(exc) or attempt == cfg.max_retries:
                            raise
                        wait = delay_for(attempt, cfg)
                        _log_retry(op, attempt, cfg.max_retries, wait, exc)
                        await asyncio.sleep(wait)
                raise last_exc  # pragma: no cover  循环内必抛出

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(1, cfg.max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001
                    last_exc = exc
                    if not is_retryable(exc) or attempt == cfg.max_retries:
                        raise
                    wait = delay_for(attempt, cfg)
                    _log_retry(op, attempt, cfg.max_retries, wait, exc)
                    time.sleep(wait)
            raise last_exc  # pragma: no cover

        return sync_wrapper

    return decorator if func is None else decorator(func)


def with_graceful_retry(func: Callable | None = None, *, config: RetryConfig | None = None,
                        default_factory: Callable[[], object] | None = None,
                        operation: str | None = None):
    """柔性重试装饰器：重试耗尽后返回兜底值，不抛异常。

    `default_factory` 用于可变/复杂默认值（如空列表、空字典），
    不传则返回 None。失败会记 ERROR 日志——柔性不等于静默。

    用法：
        @with_graceful_retry(operation="extract_entities", default_factory=list)
        async def extract(...) -> list: ...
    """
    cfg = config or DEFAULT_RETRY

    def decorator(fn: Callable) -> Callable:
        op = operation or getattr(fn, "__qualname__", str(fn))
        fallback = default_factory or (lambda: None)

        if inspect.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                for attempt in range(1, cfg.max_retries + 1):
                    try:
                        return await fn(*args, **kwargs)
                    except BaseException as exc:  # noqa: BLE001
                        if not is_retryable(exc) or attempt == cfg.max_retries:
                            logger.error("{} 最终失败，返回兜底值：{}", op,
                                         mask_secret(_describe(exc)))
                            return fallback()
                        wait = delay_for(attempt, cfg)
                        _log_retry(op, attempt, cfg.max_retries, wait, exc)
                        await asyncio.sleep(wait)

            return async_wrapper

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            for attempt in range(1, cfg.max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001
                    if not is_retryable(exc) or attempt == cfg.max_retries:
                        logger.error("{} 最终失败，返回兜底值：{}", op,
                                     mask_secret(_describe(exc)))
                        return fallback()
                    wait = delay_for(attempt, cfg)
                    _log_retry(op, attempt, cfg.max_retries, wait, exc)
                    time.sleep(wait)

        return sync_wrapper

    return decorator if func is None else decorator(func)
