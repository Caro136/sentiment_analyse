"""实时事件流接口（PRD FR-69）。

前端用 `EventSource` 订阅这个接口，拿到角色进度、章节完成与错误事件。

三个实现细节：

1. **心跳必须发**。中间的 Nginx、浏览器、代理都会对"长时间没有数据的连接"
   做超时断开——研判任务可能几分钟没有新事件（LLM 正在生成章节），
   没有心跳的话连接会被静默掐断，表现为"进度条卡住不再更新"。
2. **`X-Accel-Buffering: no`**。Nginx 默认会缓冲响应，
   即使后端持续推送，前端也要攒够一个缓冲区才收到——实时性完全丧失。
3. **连接建立时先发一条 ready**，让前端能区分"连接成功但暂无事件"
   与"连接失败"。
"""

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.services.realtime import get_broadcaster
from engines.common.logging import logger
from engines.contracts.events import EventType

router = APIRouter(prefix="/api/v1/events", tags=["realtime"])

# 心跳间隔（秒）。比常见的 60s 代理超时留足余量
HEARTBEAT_SECONDS = 15


@router.get("/stream", summary="实时事件流（SSE）")
async def stream(request: Request) -> EventSourceResponse:
    """订阅实时事件流。

    事件类型见 `engines/contracts/events.py`：角色进度、角色结果、
    角色错误、章节就绪。
    """
    broadcaster = get_broadcaster()

    async def event_generator():
        async with broadcaster.subscribe() as queue:
            # 先确认连接，前端据此区分"连上了但还没事件"与"没连上"
            yield {
                "event": "ready",
                "data": json.dumps({"status": "connected",
                                    "connections": broadcaster.connection_count},
                                   ensure_ascii=False),
            }

            while True:
                if await request.is_disconnected():
                    logger.debug("客户端已断开，结束事件流")
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(),
                                                     timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    # 纯心跳：不作为业务事件，前端可忽略
                    yield {"event": "keepalive", "data": "{}"}
                    continue
                except asyncio.CancelledError:
                    break

                yield {
                    "event": str(payload.get("event_type", "message")),
                    "data": json.dumps(payload, ensure_ascii=False),
                }

    return EventSourceResponse(
        event_generator(),
        ping=HEARTBEAT_SECONDS,
        headers={
            # 关掉 Nginx 的响应缓冲，否则 SSE 会被攒着一起发，实时性尽失
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/stats", summary="事件流状态（连接数、转发类型）")
async def stats() -> dict:
    """供联调排查用：确认广播已启动、有几个连接。"""
    return get_broadcaster().stats()


@router.get("/types", summary="可订阅的事件类型")
async def event_types() -> dict:
    """前端可据此校验事件名拼写——写错的事件名监听不到，且不报错。"""
    from app.services.realtime.broadcaster import FORWARDED_EVENT_TYPES

    return {
        "forwarded": [
            {
                "type": event_type.value,
                "description": _DESCRIPTIONS.get(event_type, ""),
            }
            for event_type in FORWARDED_EVENT_TYPES
        ],
        "heartbeat_seconds": HEARTBEAT_SECONDS,
    }


_DESCRIPTIONS = {
    EventType.ROLE_PROGRESS: "角色处理进度（驱动进度条）",
    EventType.ROLE_RESULT: "角色执行完成",
    EventType.ROLE_ERROR: "角色执行失败",
    EventType.SECTION_READY: "章节生成完成（可用于更新已完成章节列表）",
}
