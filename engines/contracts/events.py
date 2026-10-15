"""事件契约：系统内五类事件的定义。

放在 contracts 而非 common，是因为事件**跨模块共享**——发布方（编排层、各 Agent）
与订阅方（SSE 广播、主持人监听器、讨论缓冲）分属不同模块，事件模型是它们之间的契约。

五类事件（设计规格 3.2）：

| 事件 | 发布方 | 订阅方 | 用途 |
|------|--------|--------|------|
| `ROLE_PROGRESS` | 编排层 | SSE 广播 | 前端进度条 |
| `ROLE_RESULT` | 编排层 | SSE 广播 | 角色完成通知 |
| `ROLE_ERROR` | 编排层 | SSE 广播 | 失败上报 |
| `SECTION_READY` | 分析角色 | 主持人监听器 | **章节级配对研判的触发源** |
| `HOST_DISCUSSION_MESSAGE` | 主持人 | 讨论缓冲 | 研判过程可见性 |

设计约定：事件**不可变**（frozen），载荷为 Pydantic 模型以便直接序列化为 SSE 的 JSON。
"""

import time
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


def _now_ts() -> int:
    return int(time.time())


class EventType(str, Enum):
    """事件类型。取值即 SSE 推送时的类型标识。"""

    ROLE_PROGRESS = "role_progress"
    ROLE_RESULT = "role_result"
    ROLE_ERROR = "role_error"
    SECTION_READY = "section_ready"
    HOST_DISCUSSION_MESSAGE = "host_discussion_message"
    # 第 6 类：内容打标完成。前五类是研判链路的运行时事件，
    # 这一类是**业务事实**——预警引擎靠它做事件驱动的规则匹配（设计规格 7.1/8.2：
    # 告警检测是事件驱动而非轮询）。它不是给前端看的，因此不进 SSE 白名单。
    CONTENT_LABELED = "content_labeled"


class BaseEvent(BaseModel):
    """事件基类。所有事件不可变，构造即固定。"""

    model_config = ConfigDict(frozen=True)

    # ClassVar：子类声明自己的类型，不参与实例化与序列化
    event_type: ClassVar[EventType]

    occurred_at: int = Field(default_factory=_now_ts, description="事件发生时间戳")

    def to_payload(self) -> dict[str, Any]:
        """转为可 JSON 序列化的载荷（含事件类型）。"""
        return {"event_type": self.event_type.value, **self.model_dump()}


class RoleProgressEvent(BaseEvent):
    """角色处理进度：驱动前端进度条。"""

    event_type: ClassVar[EventType] = EventType.ROLE_PROGRESS

    role: str = Field(description="角色标识，见 contracts.roles")
    status: str = Field(
        description="阶段标识：planning/retrieving/searching/summary/formatting/completed"
                    "（取值与 common.progress.STAGE_RANGES 的键一致）")
    message: str = Field(default="", description="面向用户的进度说明")
    progress_pct: int = Field(default=0, ge=0, le=100, description="进度百分比")


class RoleResultEvent(BaseEvent):
    """角色执行完成。"""

    event_type: ClassVar[EventType] = EventType.ROLE_RESULT

    role: str
    result_path: str | None = Field(default=None, description="产物文件路径（如有）")


class RoleErrorEvent(BaseEvent):
    """角色执行失败。"""

    event_type: ClassVar[EventType] = EventType.ROLE_ERROR

    role: str
    error: str = Field(description="错误摘要（调用方须先经 mask_secret 脱敏）")


class SectionReadyEvent(BaseEvent):
    """章节就绪——**主持人配对研判的触发源**。

    分析角色不是等整份报告完成才交给主持人，而是每完成一章即发布本事件。
    主持人据此按 `section_key` 配对，两侧（私域 + 公域）到齐即启动该维度研判。

    跨主题隔离：`query` 一并携带，主持人据它区分不同研判任务，
    避免上一个主题的章节与本主题错误配对。
    """

    event_type: ClassVar[EventType] = EventType.SECTION_READY

    source: str = Field(description="来源角色：insight / media")
    agent_name: str = Field(default="", description="角色中文名，用于展示")
    section_key: str = Field(description="章节维度标识，配对依据")
    section_index: int = Field(default=0, description="章节序号（0 起）")
    title: str = Field(default="", description="章节标题")
    query: str = Field(description="研判主题词，用于跨主题隔离")
    body: str = Field(default="", description="章节正文 Markdown")
    section_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="辅助信息：如证据条数 hit_count、证据强度 strength",
    )


class HostDiscussionMessageEvent(BaseEvent):
    """主持人研判消息：逐维研判结论与最终裁判，用于过程可见性。

    `section_key` 为空表示最终裁判（不由某一维度产生）。
    """

    event_type: ClassVar[EventType] = EventType.HOST_DISCUSSION_MESSAGE

    source: str = Field(default="host", description="消息来源，固定为 host")
    section_key: str | None = Field(default=None, description="关联维度；空表示最终裁判")
    section_title: str = Field(default="", description="维度标题")
    message_text: str = Field(description="研判正文")
    query: str = Field(default="", description="研判主题词")
    is_final: bool = Field(default=False, description="是否最终裁判结论")


class ContentLabeledEvent(BaseEvent):
    """内容打标完成——**预警引擎的触发源**（设计规格 7.1）。

    载荷只带「哪些内容被打标了」与它们的主题归属，不带标签内容本身：
    订阅方（`engines/alerting/listener.py`）自己去分析区读标签。
    这样事件保持在几十字节量级，也不必在事件里重复一份标签数据（会有两个事实源）。

    `topic_ids` 可能为空（打标时未指定主题）——那时的告警按「未归属主题」处理。
    """

    event_type: ClassVar[EventType] = EventType.CONTENT_LABELED

    source: str = Field(default="analytics", description="发布方，固定为 analytics")
    content_uids: tuple[str, ...] = Field(default=(), description="本次打标的内容标识")
    topic_ids: tuple[str, ...] = Field(default=(), description="涉及的主题标识")
    negative_count: int = Field(default=0, description="其中负向条数（供订阅方快速判断）")
    analyzed_at: int = Field(default_factory=_now_ts)


# 事件类型 → 模型类，供反序列化与订阅端做类型校验
EVENT_MODELS: dict[EventType, type[BaseEvent]] = {
    EventType.ROLE_PROGRESS: RoleProgressEvent,
    EventType.ROLE_RESULT: RoleResultEvent,
    EventType.ROLE_ERROR: RoleErrorEvent,
    EventType.SECTION_READY: SectionReadyEvent,
    EventType.HOST_DISCUSSION_MESSAGE: HostDiscussionMessageEvent,
    EventType.CONTENT_LABELED: ContentLabeledEvent,
}
