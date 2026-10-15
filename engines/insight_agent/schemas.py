"""私域研究员的结构化输出模型。"""

from pydantic import AliasChoices, BaseModel, Field, field_validator

# ⚠️ 本模块的 schema 有两个刻意的宽容设计，都是踩坑换来的：
#
# 1. **不设 max_length**。给模型输出加长度硬约束是脆弱的——它数不清字数，
#    标题写 45 个字、要点写 35 个字都很正常，而 Pydantic 会直接判整批失败。
#    结果是「规划整个回退成固定大纲」，日志里只有一句 ValueError，很难联想到字数。
#    长度该由代码截断（见 reporting.py），而不是交给模型自律。
#
# 2. **字段名用 AliasChoices 兼容多种写法**。实测模型把 `section_key` 写成 `key`、
#    把 `entity_type` 写成 `type`——它按自己的语言习惯命名，不看 schema 说明。
#    一次不匹配就是整批作废，而兼容的成本只是一行别名。


class SectionSpec(BaseModel):
    """一个章节的规划。"""

    section_key: str = Field(
        validation_alias=AliasChoices("section_key", "key", "dimension_key"),
        description="章节维度标识，必须从给定的五个 key 中原样选取")
    title: str = Field(default="", description="章节标题，控制在 40 字以内")
    focus_points: list[str] = Field(
        default_factory=list,
        description="本章要回答的 1~3 个分析要点，每条 30 字以内")


class ResearchPlanOutput(BaseModel):
    """章节规划输出。

    模型只需给出**分析要点**，章节的最终顺序与完整性由代码按五维基准规整——
    让模型自由发挥章节结构，两侧（私域与公域）必然对不齐，
    主持人配对研判也就无从谈起。
    """

    overview: str = Field(
        default="", description="对本批证据的整体判断，说明数据支撑得起什么、缺什么")
    sections: list[SectionSpec] = Field(
        default_factory=list, description="逐维度的分析计划")

    @field_validator("sections", mode="before")
    @classmethod
    def _accept_mapping(cls, value):
        """兼容模型把 sections 写成 `{维度key: {...}}` 的形式。

        提示词里按维度罗列材料，模型很自然地用字典组织输出，
        而我们期望的是数组。硬性要求数组会让整批规划失败，转换一下成本极低。
        """
        if isinstance(value, dict):
            items = []
            for key, spec in value.items():
                if isinstance(spec, dict):
                    items.append({"section_key": key, **spec})
                else:
                    items.append({"section_key": key})
            return items
        return value


class SectionBody(BaseModel):
    """章节正文输出。"""

    body: str = Field(description="章节正文，Markdown 格式，只写正文不含标题")
    key_findings: list[str] = Field(
        default_factory=list,
        description="本章 1~3 条核心结论，供报告摘要使用")
