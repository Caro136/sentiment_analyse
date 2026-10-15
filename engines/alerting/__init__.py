"""预警引擎（T-24，对应 PRD FR-52~FR-56）。

职责：监听「内容已打标」事件 → 三类规则匹配（负向声量激增 / 敏感词命中 /
高权重账号发声）→ 分级 → 收敛去重 → 落库 → 通知下发（邮件 + 站内）→ 生命周期。

不做：修改分析结果、直接推送前端（经服务层）。

| 模块 | 职责 |
|------|------|
| `rules`      | 三类规则的**纯函数**评估（可离线测试） |
| `lifecycle`  | 告警状态迁移表（未读→已读→已认领→已关闭） |
| `repository` | `alert_rule` / `alert_record` 读写与收敛判定 |
| `service`    | 扫描编排：匹配 → 收敛 → 落库 → 通知 |
| `listener`   | 订阅 `CONTENT_LABELED`，回调 O(1) 入队，worker 串行扫描 |
| `notifiers`  | 邮件渠道（smtplib，退避重试后降级为站内） |

两条硬约束：

1. **先落库再通知**。邮件可能失败并降级，但告警记录绝不能因为通知发不出去而丢失
   ——它是后续处置的唯一凭据。
2. **收敛不重复通知**。同一规则 + 同一事件在收敛窗口内只推送一次，
   重复推送会让收件人忽略整个渠道。

**审计不在这里**：`@audit` 属于接入层（`app/services/alert`），引擎层不得反向依赖 app。
"""

from engines.alerting.lifecycle import ALLOWED_TRANSITIONS, can_transition
from engines.alerting.listener import AlertListener, get_listener, reset_listener
from engines.alerting.rules import RuleEvaluation, RuleSpec, evaluate_rules
from engines.alerting.service import (
    DEFAULT_DEDUP_MINUTES,
    DEFAULT_WINDOW_MINUTES,
    ScanResult,
    acknowledge,
    build_alert_message,
    scan,
    scan_event,
)

__all__ = [
    "RuleSpec", "RuleEvaluation", "evaluate_rules",
    "ScanResult", "scan", "scan_event", "acknowledge", "build_alert_message",
    "DEFAULT_WINDOW_MINUTES", "DEFAULT_DEDUP_MINUTES",
    "ALLOWED_TRANSITIONS", "can_transition",
    "AlertListener", "get_listener", "reset_listener",
]
