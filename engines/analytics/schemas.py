"""认知分析的结构化输出模型。

用结构化输出（function calling）而不是"让模型返回 JSON 文本再解析"：
后者的失败模式是返回一段格式微错的 JSON，解析炸掉或静默丢字段；
前者由 schema 约束，模型输出的对象直接可用。
"""

from typing import Literal

from pydantic import AliasChoices, BaseModel, Field

# 负向情绪的合法取值（与 contracts/enums.py 的 Emotion 一致）
NEGATIVE_EMOTIONS = ("愤怒", "失望", "恐慌", "质疑")


class SentimentJudgement(BaseModel):
    """一条内容的情感判断。"""

    sentiment: Literal["positive", "neutral", "negative"] = Field(
        description="情感倾向：positive 正向 / neutral 中性 / negative 负向")

    emotion: str = Field(
        default="",
        description="负向细分情绪，取值：愤怒 / 失望 / 恐慌 / 质疑。"
                    "非负向内容留空字符串")

    confidence: float = Field(
        default=0.8, ge=0.0, le=1.0,
        description="判断置信度。反讽、混合情感、纯事实陈述等边界样本应给出较低置信度")

    sensitive_hit: bool = Field(
        default=False,
        description="是否命中高危敏感话题（食品安全、人身安全、监管处罚、合规违法等）")

    reason: str = Field(
        default="", max_length=200,
        description="判断依据，一句话说明为什么是这个情感倾向")


class BatchSentimentJudgement(BaseModel):
    """批量判定结果：一次请求分析多条内容。

    批量调用的意义在成本与吞吐——逐条调用时，每条内容都要付一次
    system prompt 的 token 成本；批量后 system prompt 只付一次。
    """

    items: list["ItemJudgement"] = Field(
        default_factory=list, description="逐条判定结果，顺序与输入一致")


class ItemJudgement(BaseModel):
    """批量判定中的单条结果。

    `index` 可选，理由同 ClusterLabel：模型倾向于用数组顺序表达对应关系。
    """

    index: int | None = Field(
        default=None, description="对应输入的序号（从 0 开始）；省略时按数组顺序推断")
    sentiment: Literal["positive", "neutral", "negative"]
    emotion: str = Field(default="")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    sensitive_hit: bool = Field(default=False)


BatchSentimentJudgement.model_rebuild()


# ---------------------------------------------------------------------------
# 观点聚类
# ---------------------------------------------------------------------------

class ClusterLabel(BaseModel):
    """单个观点簇的标签。

    ⚠️ `index` **必须可选**。提示词里已经把簇写成「簇 0」「簇 1」，
    模型据此认为数组顺序已经表达了对应关系，于是不再填 index 字段——
    要求必填会让整批结果校验失败（实测 12 个簇全部失败），
    而失败是静默的：聚类看着成功，标签却退回了原文截断。
    """

    index: int | None = Field(
        default=None, description="簇编号；省略时按数组顺序推断")
    label: str = Field(max_length=24,
                       description="观点摘要短语，不超过 12 个字，如「质疑散热宣传夸大」")
    summary: str = Field(default="", max_length=120,
                         description="一句话说明这簇人在表达什么诉求或态度")


class ClusterLabelling(BaseModel):
    """对一批簇的命名结果。"""

    clusters: list[ClusterLabel] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 实体抽取
# ---------------------------------------------------------------------------

EntityType = Literal["机构", "人物", "产品", "事件", "地点"]


class ExtractedEntity(BaseModel):
    """抽取出的单个实体。

    `entity_type` 接受 `type` 作为别名：模型看到「类型」这个概念时，
    习惯性地写成 `type` 而不是 `entity_type`——实测确实如此。
    校验失败会让整条抽取结果作废（一批内容白跑），为此兼容两种写法更划算。
    """

    name: str = Field(description="实体名称，用原文中的写法")
    entity_type: EntityType = Field(
        validation_alias=AliasChoices("entity_type", "type"),
        description="实体类型")


class EntityExtraction(BaseModel):
    """一条内容的实体抽取结果。"""

    entities: list[ExtractedEntity] = Field(
        default_factory=list,
        description="内容中出现的实体。只抽取明确提到的，不要臆测")


class BatchEntityExtraction(BaseModel):
    """批量抽取结果。"""

    items: list["BatchEntityItem"] = Field(default_factory=list)


class BatchEntityItem(BaseModel):
    """批量抽取中的单条结果。"""

    index: int | None = Field(
        default=None, description="对应输入的序号（从 0 开始）；省略时按数组顺序推断")
    entities: list[ExtractedEntity] = Field(default_factory=list)


BatchEntityExtraction.model_rebuild()
