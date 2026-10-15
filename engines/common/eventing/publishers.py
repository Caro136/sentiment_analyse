"""事件发布的具名封装。

给每类事件一个具名函数，好处有三：
  1. 发布点统一，便于检索「谁在什么时机发了什么事件」；
  2. 参数校验与脱敏集中在一处（如 `publish_role_error` 统一做密钥脱敏）；
  3. 将来若把总线换成消息队列，只需改这一层。

调用方一律使用本模块的函数，不直接构造事件对象再 publish。
"""

from collections.abc import Sequence
from typing import Any

from engines.common.eventing.bus import get_event_bus
from engines.common.logging import mask_secret
from engines.contracts.events import (
    ContentLabeledEvent,
    HostDiscussionMessageEvent,
    RoleErrorEvent,
    RoleProgressEvent,
    RoleResultEvent,
    SectionReadyEvent,
)


def publish_role_progress(role: str, status: str, message: str = "",
                          progress_pct: int = 0) -> None:
    """上报角色进度（驱动前端进度条）。"""
    get_event_bus().publish(RoleProgressEvent(
        role=role, status=status, message=message, progress_pct=progress_pct))


def publish_role_result(role: str, result_path: str | None = None) -> None:
    """上报角色执行完成。"""
    get_event_bus().publish(RoleResultEvent(role=role, result_path=result_path))


def publish_role_error(role: str, error: str) -> None:
    """上报角色执行失败。错误文本统一脱敏后再入事件——它会被推送到前端并落日志。"""
    get_event_bus().publish(RoleErrorEvent(role=role, error=mask_secret(error)[:500]))


def publish_content_labeled(content_uids: Sequence[str], *,
                            topic_ids: Sequence[str] = (),
                            negative_count: int = 0) -> None:
    """通告「一批内容已打标完成」（预警引擎的触发源）。

    由 analytics 在批量打标结束后调用。**回调必须是同步且 O(1) 的**
    （见 bus 模块说明）：订阅方只入队，规则匹配交给自己的工作协程，
    否则这里会阻塞打标流程。
    """
    if not content_uids:
        return
    get_event_bus().publish(ContentLabeledEvent(
        content_uids=tuple(content_uids),
        topic_ids=tuple(dict.fromkeys(topic_ids)),
        negative_count=negative_count,
    ))


def publish_section_ready(source: str, section_key: str, query: str, body: str,
                          *, agent_name: str = "", section_index: int = 0,
                          title: str = "",
                          section_metadata: dict[str, Any] | None = None) -> None:
    """上报章节就绪——主持人配对研判的触发源。

    分析角色每完成一章即调用本函数，不要等全部章节完成再批量发布：
    章节级发布才能让主持人在两侧同维到齐时立即研判，前端也才能看到滚动过程。
    """
    get_event_bus().publish(SectionReadyEvent(
        source=source,
        section_key=section_key,
        query=query,
        body=body,
        agent_name=agent_name,
        section_index=section_index,
        title=title,
        section_metadata=section_metadata or {},
    ))


def publish_host_message(message_text: str, *, section_key: str | None = None,
                         section_title: str = "", query: str = "",
                         is_final: bool = False) -> None:
    """上报主持人研判消息。`section_key` 为空表示最终裁判。"""
    get_event_bus().publish(HostDiscussionMessageEvent(
        message_text=message_text,
        section_key=section_key,
        section_title=section_title,
        query=query,
        is_final=is_final,
    ))
