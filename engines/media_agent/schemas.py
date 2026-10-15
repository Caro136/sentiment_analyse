"""公域研究员的结构化输出模型。

沿用私域那边验证过的宽容策略：字段名加别名、结构兼容字典与数组、
**不设 max_length**（模型数不清字数，超一点就整批失败）。
"""

from pydantic import AliasChoices, BaseModel, Field, field_validator


class DimensionSearchSpec(BaseModel):
    """一个维度的检索计划。"""

    dimension_key: str = Field(
        validation_alias=AliasChoices("dimension_key", "key", "section_key"),
        description="研究维度标识，必须从给定的五个 key 中原样选取")
    search_keywords: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("search_keywords", "keywords", "queries"),
        description="该维度的 2~3 个检索词，要能搜到公开报道而不只是复述主题词")


class MediaSearchPlanOutput(BaseModel):
    """检索计划输出。

    为每个维度单独规划检索词，而不是拿一个主题词搜五次——
    「事件背景」要搜通报与起因，「公众情感」要搜评论与争议，
    用同一组词搜所有维度，五个章节会拿到高度重复的材料。
    """

    overview: str = Field(default="", description="检索策略说明，一句话")
    dimensions: list[DimensionSearchSpec] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dimensions", "sections", "plan"),
        description="逐维度的检索计划")

    @field_validator("dimensions", mode="before")
    @classmethod
    def _accept_mapping(cls, value):
        """兼容模型把 dimensions 写成 `{维度key: {...}}` 的形式。"""
        if isinstance(value, dict):
            items = []
            for key, spec in value.items():
                if isinstance(spec, dict):
                    items.append({"dimension_key": key, **spec})
                elif isinstance(spec, list):
                    items.append({"dimension_key": key, "search_keywords": spec})
                else:
                    items.append({"dimension_key": key})
            return items
        return value


class SummaryBody(BaseModel):
    """章节正文输出。

    与私域研究员的同名模型结构一致，但**各自定义**而非共享：
    两个角色的章节产出格式可能独立演化（比如公域这边以后想附上来源清单），
    强行共用一个模型会让一方的改动牵动另一方。
    """

    body: str = Field(description="章节正文，Markdown 格式，只写正文不含标题")
    key_findings: list[str] = Field(
        default_factory=list, description="本章 1~3 条核心结论，供报告摘要使用")
