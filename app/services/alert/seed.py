"""预警规则初始化：建三条与 PRD 附录 B.4 对应的默认规则。

预警引擎没有规则就是空转——而「配规则」本该是管理员在界面上做的事，
全新环境却没有界面可用（前端页面属 T-33）。这个脚本给出可直接用的起点，
之后由管理员在预警中心调整阈值。

用法：
    python -m app.services.alert.seed            # 补齐缺失的默认规则（已存在则跳过）
    python -m app.services.alert.seed --reset    # 重置为默认阈值与词库

敏感词库给的是**演示用的公开词**（质量问题、监管介入一类），不是从任何真实业务
词库里带出来的；上线前必须按企业口径替换。
"""

import argparse
import asyncio
import sys

from app.services.alert import service
from engines.alerting import repository as repo
from engines.common.db.session import dispose_engine, session_scope
from engines.contracts.enums import AlertRuleType, RiskLevel

# (规则类型, 触发等级, 阈值, 敏感词库)
# 阈值口径：激增是相对增幅（0.5 = +50%），敏感词与 KOL 是条数
DEFAULT_RULES: list[tuple[AlertRuleType, RiskLevel, float, list[str]]] = [
    (AlertRuleType.NEGATIVE_SURGE, RiskLevel.A, 0.5, []),
    (AlertRuleType.SENSITIVE_HIT, RiskLevel.S, 3,
     ["食品安全", "食物中毒", "爆炸", "起火", "致癌", "虚假宣传", "要求退款",
      "集体投诉", "监管介入", "立案调查"]),
    (AlertRuleType.KOL_VOICE, RiskLevel.B, 3, []),
]


async def run(*, reset: bool) -> int:
    created, skipped, reset_count = 0, 0, 0

    try:
        async with session_scope() as session:
            existing = {rule.rule_type: rule for rule in await repo.list_rules(session)}

            for rule_type, level, threshold, words in DEFAULT_RULES:
                current = existing.get(rule_type.value)
                if current is None:
                    await service.create_rule(
                        session, rule_type=rule_type.value, level=level.value,
                        threshold=threshold, sensitive_words=words, enabled=True)
                    created += 1
                    print(f"  新建  {rule_type.label:<12} 等级={level.value} "
                          f"阈值={threshold:g} 词库={len(words)} 词")
                    continue

                if reset:
                    await service.update_rule(session, rule_id=current.rule_id,
                                              level=level.value, threshold=threshold,
                                              sensitive_words=words, enabled=True)
                    reset_count += 1
                    print(f"  重置  {rule_type.label:<12} 等级={level.value} 阈值={threshold:g}")
                else:
                    skipped += 1
                    print(f"  跳过  {rule_type.label:<12} 已存在"
                          f"（等级={current.level} 阈值={current.threshold:g}）")
    finally:
        await dispose_engine()

    if created or reset_count:
        print("\n阈值与词库可在预警中心调整（PUT /api/v1/alerts/rules/{rule_id}），"
              "改动即时生效并留痕。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="初始化预警规则")
    parser.add_argument("--reset", action="store_true",
                        help="把已有规则重置为默认阈值、等级与词库")
    args = parser.parse_args(argv)

    print("默认预警规则（PRD 附录 B.4 的三类触发条件）：")
    try:
        return asyncio.run(run(reset=args.reset))
    except Exception as exc:  # noqa: BLE001
        print(f"失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
