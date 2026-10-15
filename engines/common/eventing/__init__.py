"""事件总线：进程内发布订阅。

- `bus`：订阅/发布实现与全局单例
- `publishers`：五类事件的具名发布函数（调用方只用这个）

⚠️ 必须单 worker 运行（设计规格 ADR-05）。事件是运行时观测用途，
    不是业务事实的可靠传递；进程重启会丢失未处理事件。
"""

from engines.common.eventing.bus import (
    EventBus,
    EventCallback,
    get_event_bus,
    reset_event_bus,
)

__all__ = ["EventBus", "EventCallback", "get_event_bus", "reset_event_bus"]
