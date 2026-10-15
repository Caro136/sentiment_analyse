"""报告引擎（T-17，对应 PRD FR-57~FR-63）。

职责：加载主持人、私域、公域三方角色产物 → 汇总统计数据
     → 聚合成 PRD 规定的**五段式报告**（事件基本信息 / 传播态势分析 /
       舆论焦点与观点矩阵 / 风险研判结论 / 应对与处置建议）
     → 生成 Markdown → 渲染自包含 HTML → 落盘。

设计要点：**薄引擎**——不订阅事件、不调用分析角色，只读磁盘上已有的产物。
研究阶段产出材料，报告阶段聚合材料，两者解耦，报告可反复重生成而不必重跑研判链。

材料不全时**拒绝生成并说清缺什么**，而不是硬凑一份半成品（FR-62）。
"""

from engines.report_engine.engine import (
    ReportOutput,
    default_client,
    generate_report,
    reset_client,
)
from engines.report_engine.loader import (
    ReportInputs,
    collect_stats,
    load_inputs,
    load_reports,
)
from engines.report_engine.renderer import markdown_to_html_body, render_html

__all__ = [
    "generate_report", "ReportOutput", "default_client", "reset_client",
    "ReportInputs", "load_inputs", "load_reports", "collect_stats",
    "render_html", "markdown_to_html_body",
]
