"""情感与情绪识别（LLM 调用）。

调用形态的选择：**并发单条**而非批量。批量（一次请求判定多条）能省下
重复的 system prompt token，但解析复杂度与"模型漏判/错位"的风险都不低。
打标是离线批处理，用 5 并发把 1000 条压到十分钟量级已经够用——
先要正确，再谈成本。

失败**不由本模块兜底**：这里让异常抛出去，由 service 层统一转成
"低置信度中性"并记录，避免"模型挂了"和"模型判成中性"在数据里无法区分。
"""

import time
from datetime import datetime, timedelta, timezone

from engines.analytics.prompts import SENTIMENT_SYSTEM, SENTIMENT_USER_TEMPLATE
from engines.analytics.schemas import SentimentJudgement
from engines.common.llm_client import LLMClient
from engines.contracts.content import UnifiedContent
from engines.contracts.roles import ROLE_ANALYTICS

CST = timezone(timedelta(hours=8))

# 送入模型的正文字符上限。论坛长帖可能几千字，超出部分对情感判断的边际贡献很低，
# 却显著推高 token 成本与超时风险。
MAX_TEXT_LENGTH = 1500

_client: LLMClient | None = None


def default_client() -> LLMClient:
    """认知分析角色的客户端（模块级缓存，避免每次调用重建连接对象）。"""
    global _client
    if _client is None:
        _client = LLMClient.from_role(ROLE_ANALYTICS)
    return _client


def reset_client() -> None:
    """丢弃缓存的客户端（配置热更新后调用）。"""
    global _client
    _client = None


def _format_time(timestamp: int) -> str:
    if not timestamp:
        return "未知"
    return datetime.fromtimestamp(timestamp, CST).strftime("%Y-%m-%d %H:%M")


def build_prompt(content: UnifiedContent) -> str:
    """构造单条内容的判定提示词。

    带上来源、作者与粉丝量：同样的措辞出自 86 万粉的数码博主和零粉小号，
    风险含义完全不同，这些上下文有助于模型判断。
    """
    text = content.analysis_text[:MAX_TEXT_LENGTH]
    if len(content.analysis_text) > MAX_TEXT_LENGTH:
        text += "…（已截断）"

    return SENTIMENT_USER_TEMPLATE.format(
        source_label=content.source_type.label,
        author=content.author.nickname or "未知用户",
        followers=content.author.follower_count,
        verified=content.author.verified_type or "普通用户",
        published=_format_time(content.published_at),
        text=text,
    )


async def judge_content(content: UnifiedContent,
                        client: LLMClient | None = None) -> SentimentJudgement:
    """判定单条内容的情感倾向。

    失败时抛异常（LLM 客户端已内置重试与退避），由调用方决定如何兜底。
    """
    llm = client or default_client()
    prompt = build_prompt(content)
    started = time.monotonic()
    judgement = await llm.generate_object(prompt, SentimentJudgement,
                                          system=SENTIMENT_SYSTEM)
    judgement = _normalize(judgement)
    return judgement


def _normalize(judgement: SentimentJudgement) -> SentimentJudgement:
    """规整模型输出。

    模型偶尔会在中性/正向内容上填一个情绪标签，或者给出超出枚举的措辞。
    这类脏值若不清理，会污染后续的情绪统计。
    """
    from engines.analytics.schemas import NEGATIVE_EMOTIONS

    if judgement.sentiment != "negative":
        if judgement.emotion:
            return judgement.model_copy(update={"emotion": ""})
        return judgement

    emotion = (judgement.emotion or "").strip()
    if emotion not in NEGATIVE_EMOTIONS:
        # 负向但情绪标签不合规范：保留情感判定，清空情绪（宁可缺失也不要脏值）
        return judgement.model_copy(update={"emotion": ""})
    return judgement


def fallback_judgement(reason: str = "模型调用失败") -> SentimentJudgement:
    """失败时的兜底判断。

    用**零置信度**的中性而不是"负向"或"正向"：
    判成中性只影响统计的精确度，判成负向会造成假警报，判成正向会漏掉真风险。
    零置信度让这些条目在人工复核时能被识别出来。
    """
    return SentimentJudgement(
        sentiment="neutral", emotion="", confidence=0.0,
        sensitive_hit=False, reason=reason[:200])
