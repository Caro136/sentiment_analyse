"""五维研究框架（PRD FR-67）。

**这是本项目最重要的对齐机制**：私域研究员与公域研究员各自独立工作、互不依赖，
却必须产出结构一致的章节，否则主持人无法按维度配对比较。

五维是固定的绝对基准——章节规划交给大模型做，但无论模型输出什么，
最终都必须落回这五个 key，缺章要兜底补齐。参考实现在这里踩过坑：
让模型自由发挥章节结构，结果两侧对不齐，配对研判直接失效。

维度顺序即报告中的章节顺序，不随数据变化（聚类阶段按簇大小排序看热点，
章节规划后回到本定义的固定序）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ResearchDimension:
    """一个研究维度。

    key               维度标识，配对与调拨的依据
    index             固定序号（0 起），决定章节与报告顺序
    title             中文标题，直接用于章节标题
    insight_goal      私域研究员的分析目标（作为 Prompt 输入）
    media_goal        公域研究员的检索目标（作为 Prompt 输入）
    cluster_keywords  规则聚类兜底关键词：语义聚类不可用时，用关键词命中把内容
                      归到某个维度。语义聚类的前提是 embedding 模型可用，
                      模型缺失时必须能降级，否则整条链路断裂
    report_section    映射到报告五段式的哪一节（PRD 附录 B.6）
    """

    key: str
    index: int
    title: str
    insight_goal: str
    media_goal: str
    cluster_keywords: tuple[str, ...]
    report_section: str


DIMENSIONS: dict[str, ResearchDimension] = {
    "background_overview": ResearchDimension(
        key="background_overview",
        index=0,
        title="事件背景与概览",
        insight_goal=(
            "梳理事件的起因、涉及主体与关键时间节点，明确「发生了什么、涉及谁、"
            "从何时开始」，给出事件的整体轮廓与当前所处阶段。"
        ),
        media_goal=(
            "检索事件的权威报道、官方通报与背景资料，还原事件经过与时间线，"
            "补齐私域数据中缺失的起因与官方口径。"
        ),
        cluster_keywords=("起因", "背景", "通报", "经过", "时间线", "涉及", "起源", "公告"),
        report_section="事件基本信息",
    ),
    "heat_and_spread": ResearchDimension(
        key="heat_and_spread",
        index=1,
        title="舆情热度与传播",
        insight_goal=(
            "分析声量走势与传播路径，识别首发信源、关键引爆节点与高权重扩散账号，"
            "判断当前处于上升期、平台期还是回落期。"
        ),
        media_goal=(
            "检索事件的传播报道与转载情况，识别关键传播节点与媒体介入时点，"
            "还原公开网络中的传播脉络。"
        ),
        cluster_keywords=("热度", "传播", "转发", "声量", "发酵", "扩散", "热搜", "阅读量"),
        report_section="传播态势分析",
    ),
    "sentiment_and_opinion": ResearchDimension(
        key="sentiment_and_opinion",
        index=2,
        title="公众情感与观点",
        insight_goal=(
            "归纳公众的情感分布与核心观点，区分主流诉求与少数声音，"
            "提炼争议焦点与代表性言论。"
        ),
        media_goal=(
            "检索媒体评论与公众讨论，归纳舆论场中的主要观点与态度倾向，"
            "对照私域结论识别共识与分歧。"
        ),
        cluster_keywords=("失望", "愤怒", "质疑", "支持", "观点", "评论", "争议", "态度"),
        report_section="舆论焦点与观点矩阵",
    ),
    "platform_and_group_diff": ResearchDimension(
        key="platform_and_group_diff",
        index=3,
        title="平台与群体差异",
        insight_goal=(
            "比较不同信源与不同人群的表达差异，说明各平台讨论的侧重点差异"
            "及其可能的原因。"
        ),
        media_goal=(
            "检索不同媒体立场的报道差异，识别主流媒体与自媒体的措辞区别，"
            "补充跨平台的视角对比。"
        ),
        cluster_keywords=("平台", "群体", "网友", "年轻人", "家长", "消费者", "行业", "媒体"),
        report_section="舆论焦点与观点矩阵",
    ),
    "deep_causes_and_impact": ResearchDimension(
        key="deep_causes_and_impact",
        index=4,
        title="深层原因与影响",
        insight_goal=(
            "剖析事件背后的深层成因与责任归属，评估对品牌声誉、法务合规"
            "与经营业务的潜在影响，提示后续风险点。"
        ),
        media_goal=(
            "检索行业分析与专家观点，了解同类事件的历史处置与影响，"
            "为风险评估与应对建议提供外部参照。"
        ),
        cluster_keywords=("原因", "责任", "影响", "风险", "合规", "监管", "整改", "建议"),
        report_section="风险研判结论",
    ),
}

# 固定顺序的维度列表（章节与报告都按这个顺序）
_ORDERED = tuple(sorted(DIMENSIONS.values(), key=lambda d: d.index))


def get_dimensions() -> tuple[ResearchDimension, ...]:
    """按固定顺序返回全部维度。"""
    return _ORDERED


def dimension_keys() -> tuple[str, ...]:
    return tuple(d.key for d in _ORDERED)


def get_dimension(key: str) -> ResearchDimension:
    """按 key 取维度。未知 key 直接报错——静默兜底会让章节悄悄错位。"""
    try:
        return DIMENSIONS[key]
    except KeyError:
        known = ", ".join(dimension_keys())
        raise KeyError(f"未知研究维度 {key!r}，可用维度：{known}") from None


def dimension_index(key: str) -> int:
    return get_dimension(key).index


def normalize_section_keys(keys: list[str]) -> list[str]:
    """把任意章节 key 列表规整为「固定五维、顺序正确、不缺不重」。

    章节规划由大模型产出，可能缺章、多章或顺序错乱。这里是唯一的兜底入口：
    以固定五维为绝对基准重排，缺失的补上，多余的丢弃。
    """
    known = set(DIMENSIONS)
    seen = [k for k in keys if k in known]
    ordered = sorted(set(seen), key=dimension_index)
    for key in dimension_keys():
        if key not in ordered:
            ordered.append(key)
    return sorted(ordered, key=dimension_index)
