"""主持人研判的结构化输出模型。

两点与前几个模块一致的经验：

1. **不设 max_length**——模型数不清字数，硬约束会让整批结果校验失败；
2. **字段名用 AliasChoices 容错**——模型习惯把 consensus 写成 agreements、
   divergence 写成 conflicts，硬性要求某一种写法就是整批判研作废。

研判是**逐维度调用**的，所以每次只产出该维度的结论，
不需要在模型里带维度标识——调用方自己知道在研判哪个维度。
"""

from pydantic import AliasChoices, BaseModel, Field


class SectionJudgementOutput(BaseModel):
    """单个维度的研判结论。"""

    consensus: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("consensus", "agreements", "共识"),
        description="两个来源互相印证的结论，每条不超过 40 字")

    divergence: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("divergence", "conflicts", "分歧"),
        description="两个来源存在分歧或矛盾之处；没有分歧就返回空数组")

    risks: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("risks", "risk_points", "风险"),
        description="本维度暴露的风险点或信息缺口")

    summary: str = Field(
        default="", description="本维度的综合研判结论，一到两句话")


class FinalReportOutput(BaseModel):
    """最终裁判。"""

    conclusion: str = Field(
        default="",
        validation_alias=AliasChoices("conclusion", "summary", "结论"),
        description="对整体舆情的综合判断，三到五句话")

    key_risks: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("key_risks", "risks", "主要风险"),
        description="需要优先关注的风险点")

    recommendations: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("recommendations", "advice", "建议"),
        description="给决策方的行动建议")

    confidence: float = Field(
        default=0.7, ge=0.0, le=1.0,
        description="对本次研判的整体置信度；材料明显不足时应给出较低值")
