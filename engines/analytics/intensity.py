"""舆情烈度指数与风险分级（PRD FR-22 / FR-23）。

烈度指数是四项因子的加权和：

    烈度 = w_负向 × 负向强度
         + w_声量 × 传播规模
         + w_KOL  × 账号影响力
         + w_敏感 × 敏感词命中

**四项权重来自配置**（`settings.intensity_weights`，对应 PRD Q1）——
它们是业务判断而非技术常量，必须可以在不改代码的前提下调整。
数据侧的风险评测集用的是同一组权重与同一套公式，
改这里就等于改标注口径，需要同步重跑 `risk_eval`。

为什么不做"绝对声量阈值"：舆情的互动量跨度极大（普通用户几条、KOL 几十万），
任何固定阈值都会在某个量级上失效。这里用对数归一化，
让"从 10 涨到 100"和"从 1 万涨到 10 万"在烈度上有相近的贡献。
"""

import math
from dataclasses import dataclass, field

from engines.contracts.enums import RiskLevel, Sentiment

# 传播规模饱和点：互动量达到 100 万才视为满分（log10(1e6)/6 = 1.0）。
#
# 这个值定得高是有意的。曾经用 1 万作为饱和点，结果一条百来条互动的普通负面评论
# 也能算出 0.6 的烈度、被判成 A 级高危——分级随之失去区分度，
# 「只看 S/A 级」的筛选功能也就没用了。
# 单条内容的互动量跨度极大（普通评论几条到爆款几百万），
# 饱和点必须落在分布的长尾处，分级才有分辨力。
VOLUME_LOG_SATURATION = 6.0

# 疑似水军的烈度折扣：刷量内容不该被当作真实舆情强度
BOT_INTENSITY_DISCOUNT = 0.6

# 烈度 → 风险等级。**与数据侧评测集（data/generator/eval_builder.py）保持一致**，
# 改动必须同步，否则「模型判定」与「评测集标注」会各说各话。
LEVEL_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("S", 0.72),
    ("A", 0.58),
    ("B", 0.40),
)

# 默认权重：与 .env 的初始值一致。实际计算一律从配置读取，
# 这里的常量仅用于配置缺失时的兜底与单元测试。
DEFAULT_WEIGHTS: dict[str, float] = {
    "negative": 0.50,
    "volume": 0.30,
    "kol": 0.12,
    "sensitive": 0.15,
}


@dataclass(frozen=True)
class IntensityInput:
    """烈度计算的输入。"""

    sentiment: str
    confidence: float = 1.0
    engagement_total: int = 0
    influence_score: float = 0.0
    sensitive_hit: bool = False
    is_suspected_bot: bool = False


@dataclass(frozen=True)
class IntensityResult:
    """烈度计算结果。`factors` 保留各项取值，供界面展示判定依据（FR-23）。"""

    index: float
    risk_level: RiskLevel
    factors: dict[str, float] = field(default_factory=dict)

    @property
    def level_label(self) -> str:
        return self.risk_level.label


def negative_score(sentiment: str, confidence: float = 1.0) -> float:
    """负向强度：只有负向内容计入，中性与正向都是 0。

    不把中性算作"半个负向"——那会让大量无关的中性讨论推高风险等级，
    使 S/A 级判定失去区分度。
    """
    if sentiment != Sentiment.NEGATIVE.value:
        return 0.0
    return min(max(confidence, 0.0), 1.0)


def volume_score(engagement_total: int) -> float:
    """传播规模：互动量取对数后归一化。"""
    if engagement_total <= 0:
        return 0.0
    return min(math.log10(engagement_total + 1) / VOLUME_LOG_SATURATION, 1.0)


def kol_score(influence_score: float) -> float:
    """账号影响力：影响力评分（0~100）归一化。"""
    return min(max(influence_score, 0.0) / 100.0, 1.0)


def level_of(index: float) -> RiskLevel:
    """烈度指数 → 风险等级。"""
    for name, threshold in LEVEL_THRESHOLDS:
        if index >= threshold:
            return RiskLevel(name)
    return RiskLevel.C


def compute_intensity(data: IntensityInput,
                      weights: dict[str, float] | None = None) -> IntensityResult:
    """计算烈度指数与风险等级。

    `weights` 传 None 时用默认值；生产代码一律传 `settings.intensity_weights`。
    """
    w = weights or DEFAULT_WEIGHTS

    factors = {
        "negative": negative_score(data.sentiment, data.confidence),
        "volume": volume_score(data.engagement_total),
        "kol": kol_score(data.influence_score),
        "sensitive": 1.0 if data.sensitive_hit else 0.0,
    }

    index = (w.get("negative", 0.0) * factors["negative"]
             + w.get("volume", 0.0) * factors["volume"]
             + w.get("kol", 0.0) * factors["kol"]
             + w.get("sensitive", 0.0) * factors["sensitive"])

    if data.is_suspected_bot:
        index *= BOT_INTENSITY_DISCOUNT

    index = round(min(max(index, 0.0), 1.0), 4)
    return IntensityResult(index=index, risk_level=level_of(index), factors=factors)


def explain(result: IntensityResult, weights: dict[str, float] | None = None) -> str:
    """把判定依据渲染成人能看懂的一句话，用于界面展示与报告。

    「为什么判定为 S 级」必须可解释——否则分析师无法信任也无法复核这个结论。
    """
    w = weights or DEFAULT_WEIGHTS
    parts = []
    labels = {
        "negative": "负向强度", "volume": "传播规模",
        "kol": "账号影响", "sensitive": "敏感词",
    }
    for key, label in labels.items():
        contribution = w.get(key, 0.0) * result.factors.get(key, 0.0)
        parts.append(f"{label} {result.factors.get(key, 0.0):.2f}×{w.get(key, 0.0)}={contribution:.3f}")
    return (f"烈度 {result.index:.3f} → {result.risk_level.value} 级"
            f"（{result.risk_level.label}）：" + "，".join(parts))
