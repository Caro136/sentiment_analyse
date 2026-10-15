"""认知分析：情感识别、舆情烈度、风险分级、实体与聚类。

本层是接入层与研判层之间的**共享分析底座**：内容入库即打标，
大盘、筛选、预警、研判都消费它的产出（设计规格 ADR-02）。

已实现：
  - intensity   烈度指数与风险分级（FR-22 / FR-23）
  - sentiment   情感与情绪识别（FR-20 / FR-21）
  - schemas     结构化输出模型
  - prompts     判定提示词
  - repository  content_analysis 读写（FR-24 人工复核保护）
  - service     批量打标编排
待实现：
  - entities    命名实体与关系（FR-25 / FR-26，T-11）
  - clustering  语义聚类与观点聚类（FR-27 / FR-28，T-11）
"""

from engines.analytics.intensity import (
    DEFAULT_WEIGHTS,
    LEVEL_THRESHOLDS,
    IntensityInput,
    IntensityResult,
    compute_intensity,
    explain,
    level_of,
)
from engines.analytics.schemas import SentimentJudgement
from engines.analytics.service import (
    MODEL_VERSION,
    AnalysisResult,
    AnalysisStats,
    analyze_contents,
)

__all__ = [
    "IntensityInput", "IntensityResult", "compute_intensity", "level_of", "explain",
    "DEFAULT_WEIGHTS", "LEVEL_THRESHOLDS",
    "SentimentJudgement",
    "analyze_contents", "AnalysisResult", "AnalysisStats", "MODEL_VERSION",
]
