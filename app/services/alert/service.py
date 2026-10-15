"""预警用例层（FR-52~FR-56）：告警查询、生命周期流转、规则维护、补扫入口。

引擎层（`engines/alerting`）负责「怎么判定与下发」，本层负责「谁能做、做了要留痕」——
因此**审计只在这里**（引擎层不得反向依赖 app 层）。

三处值得说明：

1. **状态流转要留痕**。认领与关闭都挂 `ALERT_ACK` 审计：告警的处置过程是事故复盘的
   主要材料，只靠 `responded_at` 一个时间戳说不清「谁在什么时候把它关了」。
2. **规则变更同样留痕**（`CONFIG_UPDATE`）：FR-52 明确要求「管理员可将阈值由 50%
   调整为 30% 并保存」，这类改动必须能查到前后值——否则「为什么昨天没报警」无从回答。
3. **补扫入口**是给存量数据用的：T-24 之前打标的内容不会再有事件，
   靠它把历史告警补出来（也可用于演示）。
"""

from app.errors import conflict, invalid_request, not_found
from app.services.audit import audit, note_change
from app.services.system.tasks import track_task
from engines.alerting import repository as repo
from engines.alerting import service as engine
from engines.alerting.lifecycle import can_transition, is_terminal, transition_error
from engines.contracts.enums import AlertRuleType, AlertStatus, AuditAction, RiskLevel

__all__ = [
    "list_alerts",
    "update_status",
    "list_rules",
    "create_rule",
    "update_rule",
    "scan_now",
    "to_alert_view",
    "to_rule_view",
]

# 规则类型的默认阈值（新建时未指定则用它，避免出现「阈值 null 导致规则永不触发」）
DEFAULT_THRESHOLDS: dict[str, float] = {
    AlertRuleType.NEGATIVE_SURGE.value: 0.5,   # 环比 +50%
    AlertRuleType.SENSITIVE_HIT.value: 5,      # 命中 5 条
    AlertRuleType.KOL_VOICE.value: 3,          # 3 条高权重发声
}


# ---------------------------------------------------------------------------
# 视图组装
# ---------------------------------------------------------------------------

def to_alert_view(snapshot: repo.AlertRecordSnapshot) -> dict:
    """把存储快照转成接口视图。

    `reason` / `topic_id` 从 `detail` 里提上来当一等字段：前端列表要按它们排序与展示，
    埋在 JSON 里会逼着前端解析明细。
    """
    detail = dict(snapshot.detail or {})
    rule_type = detail.get("rule_type")
    try:
        rule_label = AlertRuleType(rule_type).label if rule_type else ""
    except ValueError:
        rule_label = str(rule_type or "")

    return {
        "alert_id": snapshot.alert_id,
        "rule_id": snapshot.rule_id,
        "rule_type": rule_type,
        "rule_label": rule_label,
        "event_id": snapshot.event_id,
        "topic_id": detail.get("topic_id"),
        "level": snapshot.level,
        "status": snapshot.status,
        "status_label": AlertStatus(snapshot.status).label,
        "triggered_at": snapshot.triggered_at,
        "responded_at": snapshot.responded_at,
        "response_seconds": snapshot.response_seconds,
        "merged_count": snapshot.merged_count,
        "reason": detail.get("reason", ""),
        "detail": detail,
        "notify_status": snapshot.notify_status,
        "notify_error": snapshot.notify_error,
    }


def to_rule_view(record: repo.RuleRecord) -> dict:
    return {
        "rule_id": record.rule_id,
        "rule_type": record.rule_type,
        "type_label": AlertRuleType(record.rule_type).label,
        "level": record.level,
        "threshold": record.threshold,
        "sensitive_words": list(record.sensitive_words),
        "enabled": record.enabled,
        "updated_at": record.updated_at,
    }


# ---------------------------------------------------------------------------
# 告警查询与生命周期
# ---------------------------------------------------------------------------

async def list_alerts(session, *, page: int = 1, page_size: int = 20,
                      status: str | None = None, level: str | None = None,
                      event_id: str | None = None, start_time: int | None = None,
                      end_time: int | None = None) -> tuple[int, list[dict]]:
    total, rows = await repo.list_alerts(session, page=page, page_size=page_size,
                                        status=status, level=level, event_id=event_id,
                                        start_time=start_time, end_time=end_time)
    return total, [to_alert_view(row) for row in rows]


@audit(AuditAction.ALERT_ACK, target="alert:{alert_id}")
async def update_status(session, *, alert_id: str, status: str,
                        note: str = "") -> dict:
    """认领 / 关闭 / 标记已读（FR-56）。

    非法迁移会被引擎层拒绝（BIZ_203）；重复置为同一状态**不写库**——
    前端重放同一个动作不该刷新 `responded_at`（那会让响应时长指标失真）。
    但这次请求仍进审计，并以 `changed=false` 标注「未发生变更」：
    与风险复核（FR-24）保持同一套语义，审计表里「有什么」才是可预测的。
    """
    current = await repo.get_alert(session, alert_id)
    if current is None:
        raise not_found(f"告警不存在：{alert_id}", detail=f"alert_id={alert_id}")

    if current.status == status:
        note_change(before={"status": current.status, "responded_at": current.responded_at},
                    after={"status": current.status,
                           "responded_at": current.responded_at,
                           "changed": False})
        return to_alert_view(current)

    updated = await engine.acknowledge(session, alert_id, status=status)
    note_change(
        before={"status": current.status, "responded_at": current.responded_at},
        after={"status": updated.status, "responded_at": updated.responded_at,
               "note": (note or "").strip()},
    )
    return to_alert_view(updated)


# ---------------------------------------------------------------------------
# 规则维护（FR-53）
# ---------------------------------------------------------------------------

async def list_rules(session) -> list[dict]:
    return [to_rule_view(rule) for rule in await repo.list_rules(session)]


@audit(AuditAction.CONFIG_UPDATE, target="alert_rule:{rule_type}")
async def create_rule(session, *, rule_type: str, level: str,
                      threshold: float | None = None, sensitive_words: list[str] | None = None,
                      enabled: bool = True) -> dict:
    """新建规则。敏感词规则必须给词库——没有词库的敏感词规则永远不会命中。"""
    words = [word.strip() for word in (sensitive_words or []) if word.strip()]
    if rule_type == AlertRuleType.SENSITIVE_HIT.value and not words:
        raise invalid_request("敏感词规则必须提供敏感词库")

    if threshold is None:
        threshold = DEFAULT_THRESHOLDS.get(rule_type, 1.0)

    record = await repo.create_rule(session, rule_id=repo.new_rule_id(),
                                    rule_type=rule_type, level=level,
                                    threshold=threshold,
                                    sensitive_words=",".join(words), enabled=enabled)
    note_change(after=to_rule_view(record))
    return to_rule_view(record)


@audit(AuditAction.CONFIG_UPDATE, target="alert_rule:{rule_id}")
async def update_rule(session, *, rule_id: str, level: str | None = None,
                      threshold: float | None = None,
                      sensitive_words: list[str] | None = None,
                      enabled: bool | None = None) -> dict:
    """更新规则的阈值 / 等级 / 词库 / 启停。**下一次扫描即时生效**。"""
    current = await repo.get_rule(session, rule_id)
    if current is None:
        raise not_found(f"预警规则不存在：{rule_id}", detail=f"rule_id={rule_id}")

    fields: dict = {}
    if level is not None:
        fields["level"] = RiskLevel(level).value
    if threshold is not None:
        fields["threshold"] = threshold
    if sensitive_words is not None:
        words = [word.strip() for word in sensitive_words if word.strip()]
        if current.rule_type == AlertRuleType.SENSITIVE_HIT.value and not words:
            raise invalid_request("敏感词规则不能把词库清空——那会让它永不命中")
        fields["sensitive_words"] = ",".join(words)
    if enabled is not None:
        fields["enabled"] = bool(enabled)

    if not fields:
        return to_rule_view(current)

    updated = await repo.update_rule(session, rule_id, fields)
    if updated is None:
        raise not_found(f"预警规则不存在：{rule_id}")

    note_change(before=to_rule_view(current), after=to_rule_view(updated))
    return to_rule_view(updated)


# ---------------------------------------------------------------------------
# 补扫
# ---------------------------------------------------------------------------

async def scan_now(session, task_repo, *,
                   window_minutes: int = engine.DEFAULT_WINDOW_MINUTES,
                   dedup_minutes: int = engine.DEFAULT_DEDUP_MINUTES,
                   notify: bool = True) -> dict:
    """立即扫描一次（存量数据补告警、演示、排障）。

    与事件驱动路径共用同一条链路，因此不会出现「手动扫能报、自动扫不报」的差异。
    扫描过程写入 `task_run`（FR-72 的运维看板要能看见预警任务）；
    `task_repo` 由路由注入，测试可换成替身。
    """
    if window_minutes <= 0 or dedup_minutes <= 0:
        raise invalid_request("窗口与收敛时间必须为正整数")

    async with track_task(task_repo, task_type="alert",
                          input_summary=f"window={window_minutes}min "
                                        f"dedup={dedup_minutes}min notify={notify}"):
        result = await engine.scan(session=session, window_minutes=window_minutes,
                                   dedup_minutes=dedup_minutes, notify=notify)
    return {
        "scanned_topics": result.scanned_topics,
        "window_contents": result.window_contents,
        "hits": result.hits,
        "created": result.created,
        "merged": result.merged,
        "notified": result.notified,
        "degraded": result.degraded,
        "skipped": result.skipped,
        "summary": result.render(),
    }
