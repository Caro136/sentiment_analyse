"""告警生命周期（FR-56）。

```
未读 unread ──已读 read ──已认领 claimed ──已关闭 closed
   └──────────┴─────────────┴──────────────┘      （任意状态都可直接关闭）
```

**状态迁移写成表而不是散落的 if**：告警状态是这个模块对外承诺的语义，
散落的判断迟早出现「从已关闭回到未读」这种不该存在的路径，而且不会有任何报错。
`tests/test_alerting.py` 会遍历所有 (当前状态 × 目标状态) 组合验证这张表。
"""

from engines.contracts.enums import AlertStatus

__all__ = ["ALLOWED_TRANSITIONS", "can_transition", "is_terminal", "transition_error"]

# 当前状态 → 允许迁移到的状态
ALLOWED_TRANSITIONS: dict[AlertStatus, frozenset[AlertStatus]] = {
    AlertStatus.UNREAD: frozenset({AlertStatus.READ, AlertStatus.CLAIMED,
                                  AlertStatus.CLOSED}),
    AlertStatus.READ: frozenset({AlertStatus.CLAIMED, AlertStatus.CLOSED}),
    AlertStatus.CLAIMED: frozenset({AlertStatus.CLOSED, AlertStatus.READ}),
    # 已关闭是终态：不再允许任何流转（重新打开应当是一条新告警）
    AlertStatus.CLOSED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """判断迁移是否合法。未知状态一律拒绝。"""
    try:
        current_status = AlertStatus(current)
        target_status = AlertStatus(target)
    except ValueError:
        return False
    if current_status is target_status:
        # 重复置为同一状态不视作错误（前端可能重放同一个动作），由调用方决定是否写库
        return True
    return target_status in ALLOWED_TRANSITIONS[current_status]


def is_terminal(status: str) -> bool:
    try:
        return not ALLOWED_TRANSITIONS[AlertStatus(status)]
    except ValueError:
        return False


def transition_error(current: str, target: str) -> str:
    """给出可读的拒绝原因（用于 BIZ_203 的 message）。

    措辞避开「已」字重复：状态标签本身就以「已」开头（已关闭），
    写成 `告警已{label}` 会得到「告警已已关闭」——联调时真出现过。
    """
    if is_terminal(current):
        return f"告警当前状态为「{AlertStatus(current).label}」，不能再变更状态"
    allowed = "/".join(sorted(status.value
                             for status in ALLOWED_TRANSITIONS[AlertStatus(current)]))
    return f"不允许从「{AlertStatus(current).label}」流转到「{target}」，可选：{allowed}"
