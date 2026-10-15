"""预警服务（T-24，对应 PRD FR-52~FR-56）。

| 模块 | 职责 |
|------|------|
| `service` | 告警查询、生命周期流转、规则维护、补扫入口（**审计只在这一层**） |

规则的判定与下发在 `engines/alerting`；本层负责权限与留痕。
"""

from app.services.alert.service import (
    create_rule,
    list_alerts,
    list_rules,
    scan_now,
    update_rule,
    update_status,
)

__all__ = ["list_alerts", "update_status", "list_rules", "create_rule",
           "update_rule", "scan_now"]
