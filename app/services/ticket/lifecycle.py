"""处置工单状态机（FR-49）。

```
待研判 pending ──跟踪中 tracking ──已核实 verified ──辟谣处置 debunked
      │               │                │                 │
      └───────────────┴────────────────┴─────────────────┴──→ 已处理 resolved
                                                          └──→ 已忽略 ignored
```

三条规则都写在这张表里，不散落到服务代码：

1. **只能向前**：「已核实」的工单不能再退回「待研判」——处置是要留痕的事实，
   允许回退会让轨迹失去意义；判错了就在批注里说明，而不是改历史。
2. **终态不再流转**：已处理 / 已忽略都是终态，重新打开应当是一条新工单。
3. **关闭要按角色分**：`resolved`（已处理）与 `ignored`（已忽略）属关闭动作，
   权限点 `ticket:close` **仅公关**（PRD 第 2 章）；其余流转分析师与公关共享。
"""

from engines.contracts.enums import TicketStatus

__all__ = [
    "ALLOWED_TRANSITIONS",
    "CLOSING_STATUSES",
    "can_transition",
    "is_closing",
    "transition_error",
]

# 当前状态 → 允许迁移到的状态（不含自身；置为同一状态由服务层按 no-op 处理）
ALLOWED_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.PENDING: frozenset({TicketStatus.TRACKING, TicketStatus.VERIFIED,
                                     TicketStatus.RESOLVED, TicketStatus.IGNORED}),
    TicketStatus.TRACKING: frozenset({TicketStatus.VERIFIED, TicketStatus.DEBUNKED,
                                      TicketStatus.RESOLVED, TicketStatus.IGNORED}),
    TicketStatus.VERIFIED: frozenset({TicketStatus.DEBUNKED, TicketStatus.RESOLVED,
                                      TicketStatus.IGNORED}),
    TicketStatus.DEBUNKED: frozenset({TicketStatus.RESOLVED, TicketStatus.IGNORED}),
    TicketStatus.RESOLVED: frozenset(),
    TicketStatus.IGNORED: frozenset(),
}

# 关闭动作：需要 `ticket:close`（仅公关）
CLOSING_STATUSES = frozenset({TicketStatus.RESOLVED, TicketStatus.IGNORED})


def can_transition(current: str, target: str) -> bool:
    """迁移是否合法。未知状态一律拒绝。"""
    try:
        current_status = TicketStatus(current)
        target_status = TicketStatus(target)
    except ValueError:
        return False
    if current_status is target_status:
        return True
    return target_status in ALLOWED_TRANSITIONS[current_status]


def is_closing(target: str) -> bool:
    """目标状态是否为关闭动作。"""
    try:
        return TicketStatus(target) in CLOSING_STATUSES
    except ValueError:
        return False


def transition_error(current: str, target: str) -> str:
    """可读的拒绝原因（BIZ_203 的 message）。

    措辞避开「已」字重复：状态标签本身多以「已」开头（已核实、已处理），
    写成「工单已{label}」会得到「工单已已核实」。
    """
    try:
        current_status = TicketStatus(current)
    except ValueError:
        return f"未知的工单状态：{current}"

    if current_status.is_terminal:
        return f"工单当前状态为「{current_status.label}」，不能再流转（重新开单请新建工单）"
    allowed = "/".join(status.label for status in
                       sorted(ALLOWED_TRANSITIONS[current_status],
                              key=lambda s: s.value))
    return f"不允许从「{current_status.label}」流转到「{_label(target)}」，可选：{allowed}"


def _label(status: str) -> str:
    try:
        return TicketStatus(status).label
    except ValueError:
        return status
