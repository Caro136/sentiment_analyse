"""接入与解析（T-07~T-09，对应 PRD FR-14~FR-19）。

职责：从原始表读取内容 → 平台适配器做字段映射 → 多模态解析（OCR/ASR）
     → 文本清洗 → SimHash 去重与水军识别 → 产出 UnifiedContent。

不做：情感判断（交 analytics）、风险判定（交 analytics）、告警触发（交 alerting）。
关键约束：多模态解析失败必须放行，不得阻断内容入库（ADR-06）。
"""
