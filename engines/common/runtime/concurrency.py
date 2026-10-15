"""并发控制工具。

批量调用外部服务（模型、OCR）时必须限流：一次性把上千个请求发出去，
结果不是"快"，而是被对方限流后集体失败。
"""

import asyncio
from collections.abc import Awaitable, Iterable
from typing import Any

DEFAULT_LIMIT = 5


async def gather_limited(coros: Iterable[Awaitable[Any]],
                         limit: int = DEFAULT_LIMIT) -> list[Any]:
    """限并发地执行一批协程。

    **异常作为结果返回，不向上抛**：批量打标里一条内容失败，
    不该让整批结果一起丢掉——调用方需要逐个判断。

    用法：
        results = await gather_limited([analyze(r) for r in records], limit=5)
        for result in results:
            if isinstance(result, Exception):
                ...
    """
    tasks = [c for c in coros]
    if not tasks:
        return []

    semaphore = asyncio.Semaphore(max(1, limit))

    async def _run(coro: Awaitable[Any]) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*[_run(c) for c in tasks], return_exceptions=True)
