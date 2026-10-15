"""处置工单服务（T-25，对应 PRD FR-49~FR-51）。

| 模块 | 职责 |
|------|------|
| `lifecycle`  | 状态机（待研判→跟踪中→已核实→辟谣处置→已处理/已忽略）与关闭动作判定 |
| `repository` | `ticket` / `ticket_log` 读写（状态与轨迹同事务） |
| `draft`      | 公关回应草案生成（三段式，**不落库**） |
| `service`    | 用例编排：开单、流转、批注、生成与采纳草案（审计在这一层） |

三条与设计一致的边界：

1. **轨迹只在 `ticket_log` 里长**，工单表只存当前状态；
2. **关闭动作需要 `ticket:close`**（仅公关），其余流转分析师与公关共享；
3. **草案生成不落库**，采纳才写入工单并记 `draft_adopt` 轨迹——
   模型输出可能包含不实承诺，必须有人过一遍再采用。
"""

from app.services.ticket.service import (
    adopt_response_draft,
    create_ticket,
    generate_response_draft,
    get_ticket_detail,
    list_tickets,
    update_ticket,
)

__all__ = [
    "list_tickets", "get_ticket_detail", "create_ticket", "update_ticket",
    "generate_response_draft", "adopt_response_draft",
]
