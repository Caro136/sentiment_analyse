"""演示数据重置（FR-75）：把分析结果恢复到「未分析」的干净状态。

**这是全项目最危险的一个入口**，因此约束写得比功能多：

1. **只重置分析结果，不碰原始区**。原始表是数据生成器的交付物、且 ADR-03 规定本项目
   只读——重置原始数据请跑 `python data/generator/generate.py` 重新生成再导入。
   请求 `scope=raw` 会被明确拒绝，而不是「尽力而为」地删几张表。
2. **不删账号与审计**。`sys_user` / `audit_log` 永远保留：把账号清了就再也登不进来，
   把审计清了就查不到「谁执行的这次重置」——那正是最该留痕的一次操作。
3. **必须显式确认**。请求体要带 `confirm=RESET_ANALYSIS` 这种口令式字段，
   防止误点或脚本拼错参数就把一整年的分析结果清空。
4. **重置动作本身进审计**（`AuditAction.DATA_RESET`），并在响应里返回逐表删除行数。

重置之后可以重复演示：重新打标 → 重新预警 → 重新研判 → 重新出报告。
"""

import time
from dataclasses import dataclass, field
from typing import Protocol

from loguru import logger
from sqlalchemy import text

from app.errors import invalid_request
from app.services.audit import audit, note_change
from engines.contracts.enums import AuditAction

__all__ = [
    "RESET_SCOPES",
    "CONFIRM_TOKENS",
    "PROTECTED_TABLES",
    "ResetPlan",
    "ResetExecutor",
    "SqlResetExecutor",
    "preview_reset",
    "reset_demo_data",
]

# 可重置的范围 → 涉及的表（**顺序即删除顺序**，先删从表再删主表）
RESET_SCOPES: dict[str, tuple[str, ...]] = {
    # 分析区：标签、多模态产物、事件与聚类、实体图谱
    "analysis": (
        "cluster_member", "opinion_cluster", "event_content", "spread_node",
        "entity_relation", "entity", "modality_asset", "content_analysis", "event",
    ),
    # 报告产物（磁盘文件保留，只是不再登记）
    "reports": ("report_file", "report"),
    # 预警与处置（演示要能重跑「预警 → 处置」这一段）
    "alerts": ("alert_record", "ticket_log", "ticket"),
    # 大盘物化与任务状态（重置后由后台任务自动重建）
    "runtime": ("dashboard_metrics", "task_run"),
}

# 永不重置的表——每条都有理由
PROTECTED_TABLES = {
    "sys_user": "账号：清了就登不进来，重置本身也失去操作人",
    "audit_log": "审计：最该留痕的一次操作，清了就无法追责",
    "topic": "监测配置：属于使用者的配置，不是演示数据",
    "topic_keyword": "词包：同上",
    "source_channel": "信源通道：同上",
    "topic_channel": "主题通道关联：同上",
    "alert_rule": "预警规则：配置而非数据",
    "platform_account": "账号主数据：属原始区交付物",
}

# 各范围要求的确认口令（防误操作）
CONFIRM_TOKENS = {
    "analysis": "RESET_ANALYSIS",
    "reports": "RESET_REPORTS",
    "alerts": "RESET_ALERTS",
    "runtime": "RESET_RUNTIME",
    "all": "RESET_ALL",
}


@dataclass
class ResetPlan:
    """重置计划：要删哪些表、各有多少行。"""

    scope: str
    tables: list[str] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)
    deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(self.deleted.values()) or sum(self.row_counts.values())

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "tables": self.tables,
            "row_counts": self.row_counts,
            "deleted": self.deleted,
            "total_rows": self.total_rows,
            "protected_tables": PROTECTED_TABLES,
        }


def _tables_for(scope: str) -> list[str]:
    if scope == "all":
        tables: list[str] = []
        for key in ("runtime", "alerts", "reports", "analysis"):
            tables.extend(RESET_SCOPES[key])
        return tables
    if scope == "raw":
        raise invalid_request(
            "原始区不可重置（ADR-03：本项目对 9 张原始表只读）",
            detail="请用 python data/generator/generate.py 重新生成数据集后再导入")
    if scope not in RESET_SCOPES:
        raise invalid_request(f"未知的重置范围：{scope}",
                              detail="可选：" + "/".join([*RESET_SCOPES, "all"]))
    return list(RESET_SCOPES[scope])


def _check_confirmation(scope: str, confirm: str) -> None:
    """校验确认口令。

    调用前必须先用 `_tables_for(scope)` 校验范围——**顺序反了会给未知范围
    抛 KeyError**（实测：scope=raw 直接 500，而不是给出「原始区不可重置」那句有用的提示）。
    """
    expected = CONFIRM_TOKENS.get(scope)
    if expected is None:
        raise invalid_request(f"未知的重置范围：{scope}")
    if (confirm or "").strip() != expected:
        raise invalid_request(
            f"重置 {scope} 需要确认口令 {expected}",
            detail="这是不可撤销操作，请在 confirm 字段原样填入该口令")


class ResetExecutor(Protocol):
    """按表统计与清空。抽成接口是为了让重置逻辑可离线测试——
    这是全项目最危险的入口，**它必须被测到**，而不能只靠真机执行验证。
    """

    async def count(self, table: str) -> int: ...
    async def delete(self, table: str) -> int: ...


class SqlResetExecutor:
    """SQL 实现。表名只来自本模块的白名单常量，不接受外部输入。"""

    def __init__(self, session) -> None:
        self._session = session

    async def count(self, table: str) -> int:
        return int((await self._session.execute(
            text(f"SELECT COUNT(*) FROM `{table}`"))).scalar() or 0)

    async def delete(self, table: str) -> int:
        result = await self._session.execute(text(f"DELETE FROM `{table}`"))
        return int(result.rowcount or 0)


async def preview_reset(executor: ResetExecutor, *, scope: str) -> ResetPlan:
    """只统计不删除：让调用方先看清会删掉多少行。"""
    tables = _tables_for(scope)
    plan = ResetPlan(scope=scope, tables=tables)
    for table in tables:
        plan.row_counts[table] = await executor.count(table)
    return plan


@audit(AuditAction.DATA_RESET, target="reset:{scope}")
async def reset_demo_data(executor: ResetExecutor, *, scope: str, confirm: str,
                          actor: object = None, dry_run: bool = False) -> ResetPlan:
    """按范围清空演示数据（FR-75）。返回逐表删除行数。

    `dry_run=True` 时只统计——前端可以先展示影响面再要求确认。
    **只看不删也进审计**：那是「有人动过重置入口」的事实，值得留痕。

    顺序固定为「先校验范围、再校验口令」：未知范围（如 raw）要拿到
    「原始区不可重置」这句有用的提示，而不是一句口令不匹配。
    """
    tables = _tables_for(scope)
    _check_confirmation(scope, confirm)

    plan = ResetPlan(scope=scope, tables=tables)
    for table in tables:
        plan.row_counts[table] = await executor.count(table)

    if not dry_run:
        for table in tables:
            plan.deleted[table] = await executor.delete(table)

    note_change(before={"scope": scope, "row_counts": plan.row_counts},
                after={"scope": scope, "deleted": plan.deleted,
                       "dry_run": dry_run, "at": int(time.time())})
    logger.warning("演示数据重置 scope={} 删除 {} 行（操作人：{}）",
                   scope, plan.total_rows, getattr(actor, "user_id", "系统"))
    return plan
