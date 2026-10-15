"""接入与解析（T-07~T-09，对应 PRD FR-14~FR-19）。

职责：从原始表读取内容 → 平台适配器做字段映射 → 多模态解析（OCR/ASR）
     → 文本清洗 → SimHash 去重与水军识别 → 产出 UnifiedContent。

已实现：
  - adapters  四类信源的字段映射（T-07）
  - reader    按主题词包与时间窗读取原始表（T-08）
  - cleaning  文本清洗（T-08）
  - dedup     SimHash 去重与水军识别（T-08）
  - modality  OCR（MCP 云服务）与 ASR（模拟）解析、失败放行（T-09）

不做：情感判断（交 analytics）、风险判定（交 analytics）、告警触发（交 alerting）。
关键约束：多模态解析失败必须放行，不得阻断内容入库（ADR-06）。
"""
