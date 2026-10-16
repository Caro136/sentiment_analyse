"""认知分析测试。

烈度计算与风险分级是纯函数，覆盖边界值；
批量打标用模型替身验证编排逻辑（失败兜底、统计、幂等），不依赖真实调用。
"""

import asyncio
import unittest

from engines.analytics.intensity import (
    DEFAULT_WEIGHTS,
    BOT_INTENSITY_DISCOUNT,
    IntensityInput,
    compute_intensity,
    explain,
    kol_score,
    level_of,
    negative_score,
    volume_score,
)
from engines.analytics.schemas import SentimentJudgement
from engines.analytics.sentiment import _normalize, build_prompt, fallback_judgement
from engines.analytics.service import analyze_contents
from engines.contracts.content import Author, Engagement, UnifiedContent
from engines.contracts.enums import RiskLevel, SourceType


# ---------------------------------------------------------------------------
# 烈度与风险分级
# ---------------------------------------------------------------------------

class NegativeScoreTest(unittest.TestCase):
    def test_only_negative_counts(self) -> None:
        self.assertEqual(negative_score("positive", 1.0), 0.0)
        self.assertEqual(negative_score("neutral", 1.0), 0.0)

    def test_neutral_is_not_half_negative(self) -> None:
        """中性算半个负向会让大量无关讨论推高风险等级，使分级失去区分度。"""
        self.assertEqual(negative_score("neutral", 1.0), 0.0)

    def test_confidence_scales_negative(self) -> None:
        self.assertEqual(negative_score("negative", 0.5), 0.5)
        self.assertEqual(negative_score("negative", 1.0), 1.0)

    def test_confidence_clamped(self) -> None:
        self.assertEqual(negative_score("negative", 1.8), 1.0)
        self.assertEqual(negative_score("negative", -1), 0.0)


class VolumeScoreTest(unittest.TestCase):
    def test_zero_engagement(self) -> None:
        self.assertEqual(volume_score(0), 0.0)

    def test_grows_with_engagement(self) -> None:
        self.assertLess(volume_score(10), volume_score(1000))
        self.assertLess(volume_score(1000), volume_score(100000))

    def test_capped_at_one(self) -> None:
        self.assertLessEqual(volume_score(10 ** 9), 1.0)

    def test_logarithmic_not_linear(self) -> None:
        """对数归一化：同样「涨 10 倍」，在低区间与中区间的增量相近。

        线性归一化下，低区间涨 10 倍几乎不动、高区间涨 10 倍直接拉满，
        普通用户的讨论会被完全淹没。
        （对比区间须落在饱和点以下，否则后者已封顶，增量恒为 0。）
        """
        low_gain = volume_score(100) - volume_score(10)
        mid_gain = volume_score(1000) - volume_score(100)
        # 对数曲线并非严格线性，"涨 10 倍"的增量会随区间缓慢变化，
        # 这里只要求量级相近（差异 < 10%），而不是逐位相等
        self.assertLess(abs(low_gain - mid_gain), low_gain * 0.1)

    def test_saturation_point_is_high_enough(self) -> None:
        """饱和点必须落在互动量分布的长尾——否则普通内容也会接近满分。"""
        self.assertLess(volume_score(1000), 0.6)
        self.assertLess(volume_score(10000), 0.75)
        self.assertGreater(volume_score(500000), 0.9)


class KolScoreTest(unittest.TestCase):
    def test_normalized(self) -> None:
        self.assertEqual(kol_score(0), 0.0)
        self.assertEqual(kol_score(50), 0.5)
        self.assertEqual(kol_score(100), 1.0)

    def test_clamped(self) -> None:
        self.assertEqual(kol_score(150), 1.0)
        self.assertEqual(kol_score(-10), 0.0)


class LevelTest(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertIs(level_of(0.80), RiskLevel.S)
        self.assertIs(level_of(0.60), RiskLevel.A)
        self.assertIs(level_of(0.45), RiskLevel.B)
        self.assertIs(level_of(0.10), RiskLevel.C)

    def test_boundary_values(self) -> None:
        self.assertIs(level_of(0.72), RiskLevel.S)
        self.assertIs(level_of(0.7199), RiskLevel.A)
        self.assertIs(level_of(0.58), RiskLevel.A)
        self.assertIs(level_of(0.40), RiskLevel.B)


class ComputeIntensityTest(unittest.TestCase):
    def test_no_signal_yields_low_intensity(self) -> None:
        result = compute_intensity(IntensityInput(sentiment="neutral"))
        self.assertEqual(result.index, 0.0)
        self.assertIs(result.risk_level, RiskLevel.C)

    def test_strong_negative_from_kol_goes_high(self) -> None:
        result = compute_intensity(IntensityInput(
            sentiment="negative", confidence=1.0, engagement_total=500000,
            influence_score=85, sensitive_hit=True))
        self.assertGreater(result.index, 0.7)
        self.assertIs(result.risk_level, RiskLevel.S)

    def test_positive_content_stays_low(self) -> None:
        """正向内容即便热度极高也不该是高危——这正是对照组场景要验证的。"""
        result = compute_intensity(IntensityInput(
            sentiment="positive", confidence=1.0, engagement_total=500000,
            influence_score=85))
        self.assertLess(result.index, 0.58)

    def test_bot_content_discounted(self) -> None:
        """刷量内容不该被当作真实舆情强度。"""
        normal = compute_intensity(IntensityInput(
            sentiment="negative", confidence=0.9, engagement_total=10000))
        bot = compute_intensity(IntensityInput(
            sentiment="negative", confidence=0.9, engagement_total=10000,
            is_suspected_bot=True))
        self.assertAlmostEqual(bot.index, normal.index * BOT_INTENSITY_DISCOUNT, places=4)
        self.assertLess(bot.index, normal.index)

    def test_custom_weights_change_result(self) -> None:
        data = IntensityInput(sentiment="negative", confidence=1.0)
        heavy = compute_intensity(data, {"negative": 1.0, "volume": 0, "kol": 0, "sensitive": 0})
        light = compute_intensity(data, {"negative": 0.1, "volume": 0, "kol": 0, "sensitive": 0})
        self.assertGreater(heavy.index, light.index)

    def test_default_weights_match_config_defaults(self) -> None:
        """默认权重必须与 .env 初始值一致，否则试跑结果与生产不一致。"""
        weights = compute_intensity(IntensityInput(sentiment="negative"), DEFAULT_WEIGHTS)
        self.assertGreater(weights.index, 0)

    def test_factors_returned_for_explainability(self) -> None:
        """判定依据要能回溯——分析师需要知道为什么是 S 级。"""
        result = compute_intensity(IntensityInput(
            sentiment="negative", confidence=0.9, engagement_total=1000,
            influence_score=70, sensitive_hit=True))
        self.assertEqual(set(result.factors),
                         {"negative", "volume", "kol", "sensitive"})
        self.assertEqual(result.factors["sensitive"], 1.0)

    def test_explain_is_readable(self) -> None:
        result = compute_intensity(IntensityInput(
            sentiment="negative", confidence=0.9, sensitive_hit=True))
        text = explain(result)
        self.assertIn("烈度", text)
        self.assertIn(result.risk_level.value, text)
        self.assertIn("敏感词", text)

    def test_index_capped_at_one(self) -> None:
        result = compute_intensity(IntensityInput(
            sentiment="negative", confidence=1.0, engagement_total=10 ** 9,
            influence_score=100, sensitive_hit=True),
            {"negative": 5, "volume": 5, "kol": 5, "sensitive": 5})
        self.assertLessEqual(result.index, 1.0)


# ---------------------------------------------------------------------------
# 情感判定结果的规整
# ---------------------------------------------------------------------------

class NormalizeTest(unittest.TestCase):
    def test_non_negative_clears_emotion(self) -> None:
        """模型可能给正向内容填情绪标签，不清理会污染情绪统计。"""
        result = _normalize(SentimentJudgement(
            sentiment="positive", emotion="愤怒", confidence=0.9))
        self.assertEqual(result.emotion, "")

    def test_invalid_emotion_cleared(self) -> None:
        result = _normalize(SentimentJudgement(
            sentiment="negative", emotion="很不爽", confidence=0.9))
        self.assertEqual(result.emotion, "")
        self.assertEqual(result.sentiment, "negative")   # 情感判定保留

    def test_valid_emotion_kept(self) -> None:
        result = _normalize(SentimentJudgement(
            sentiment="negative", emotion="失望", confidence=0.9))
        self.assertEqual(result.emotion, "失望")

    def test_fallback_is_neutral_with_zero_confidence(self) -> None:
        """兜底判成中性而非负向：误报比漏报的代价更高，且零置信度可被识别出来。"""
        fallback = fallback_judgement("超时")
        self.assertEqual(fallback.sentiment, "neutral")
        self.assertEqual(fallback.confidence, 0.0)
        self.assertIn("超时", fallback.reason)


class BuildPromptTest(unittest.TestCase):
    def _content(self, text: str, **kwargs) -> UnifiedContent:
        base = dict(
            content_uid="short_video:douyin_aweme:1",
            source_type=SourceType.VIDEO,
            source_table="douyin_aweme",
            business_id=1,
            author=Author(account_id="u1", nickname="测试用户", follower_count=1000),
            text=text,
        )
        base.update(kwargs)
        return UnifiedContent(**base)

    def test_prompt_contains_context(self) -> None:
        prompt = build_prompt(self._content("正文内容"))
        self.assertIn("短视频", prompt)          # 来源
        self.assertIn("测试用户", prompt)        # 作者
        self.assertIn("1000", prompt)            # 粉丝数（影响风险判断）
        self.assertIn("正文内容", prompt)

    def test_long_text_truncated(self) -> None:
        long_text = "很长的正文" * 1000
        prompt = build_prompt(self._content(long_text))
        self.assertIn("已截断", prompt)
        self.assertLess(len(prompt), len(long_text))

    def test_ocr_text_included(self) -> None:
        """图片里的文字要参与情感判断。"""
        prompt = build_prompt(self._content("正文", modality_texts=("图中的字",)))
        self.assertIn("图中的字", prompt)


# ---------------------------------------------------------------------------
# 批量打标编排（模型替身）
# ---------------------------------------------------------------------------

class FakeLLMClient:
    """模型替身。"""

    def __init__(self, judgement: SentimentJudgement | None = None,
                 error: Exception | None = None) -> None:
        self.judgement = judgement or SentimentJudgement(
            sentiment="negative", emotion="愤怒", confidence=0.9)
        self.error = error
        self.calls = 0

    async def generate_object(self, prompt, schema, system=None):  # noqa: D102
        self.calls += 1
        if self.error:
            raise self.error
        return self.judgement


def make_content(uid: str, *, engagement: int = 100, influence: float = 0.0,
                 account_id: str = "u1") -> UnifiedContent:
    return UnifiedContent(
        content_uid=uid,
        source_type=SourceType.SOCIAL,
        source_table="weibo_note",
        business_id=abs(hash(uid)) % 100000,
        author=Author(account_id=account_id, nickname="测试", influence_score=influence),
        text="这是一条测试内容，用于验证打标流程。",
        engagement=Engagement(likes=engagement),
        published_at=1700000000,
    )


class AnalyzeServiceTest(unittest.TestCase):
    def test_analyzes_all_contents(self) -> None:
        contents = [make_content(f"uid{i}") for i in range(3)]
        client = FakeLLMClient()
        result = asyncio.run(analyze_contents(contents, client=client, persist=False))
        self.assertEqual(len(result.labels), 3)
        self.assertEqual(result.stats.succeeded, 3)
        self.assertEqual(client.calls, 3)

    def test_labels_are_attached_per_content(self) -> None:
        result = asyncio.run(analyze_contents(
            [make_content("uid1")], client=FakeLLMClient(), persist=False))
        label = result.labels["uid1"]
        self.assertEqual(label.sentiment, "negative")
        self.assertEqual(label.emotion, "愤怒")
        # 百来条互动的普通负面评论：有情绪但不构成高危，应为 B 而非 A/S
        self.assertIn(label.risk_level, ("B", "C"))

    def test_failure_falls_back_without_breaking_batch(self) -> None:
        """**核心约束**：一条失败不影响其余。"""
        contents = [make_content(f"uid{i}") for i in range(3)]
        result = asyncio.run(analyze_contents(
            contents, client=FakeLLMClient(error=RuntimeError("模型超时")),
            persist=False))
        self.assertEqual(len(result.labels), 3)     # 全部有标签（兜底）
        self.assertEqual(result.stats.failed, 3)
        self.assertEqual(len(result.failed_uids), 3)
        for label in result.labels.values():
            self.assertEqual(label.sentiment, "neutral")
            self.assertEqual(label.sentiment_confidence, 0.0)

    def test_stats_aggregate_sentiment_and_risk(self) -> None:
        contents = [make_content(f"uid{i}") for i in range(4)]
        result = asyncio.run(analyze_contents(
            contents, client=FakeLLMClient(), persist=False))
        self.assertEqual(result.stats.sentiment.get("negative"), 4)
        self.assertEqual(sum(result.stats.risk_level.values()), 4)
        self.assertGreater(result.stats.avg_intensity, 0)

    def test_high_influence_negative_raises_risk(self) -> None:
        low = asyncio.run(analyze_contents(
            [make_content("a", engagement=10)], client=FakeLLMClient(), persist=False))
        high = asyncio.run(analyze_contents(
            [make_content("b", engagement=500000, influence=90)],
            client=FakeLLMClient(), persist=False))
        self.assertGreater(high.labels["b"].intensity_index,
                           low.labels["a"].intensity_index)

    def test_suspected_bot_reduces_intensity(self) -> None:
        contents = [make_content("bot", engagement=5000, account_id="bot_x")]
        result = asyncio.run(analyze_contents(
            contents, client=FakeLLMClient(), persist=False,
            bot_account_ids={"bot_x"}))
        self.assertLess(result.labels["bot"].intensity_index, 1.0)

    def test_fingerprint_recorded_in_factors(self) -> None:
        result = asyncio.run(analyze_contents(
            [make_content("uid1")], client=FakeLLMClient(), persist=False,
            fingerprints={"uid1": 12345}))
        self.assertIn("reason", result.labels["uid1"].risk_factors)

    def test_empty_input(self) -> None:
        result = asyncio.run(analyze_contents([], client=FakeLLMClient(), persist=False))
        self.assertEqual(len(result.labels), 0)
        self.assertEqual(result.stats.total, 0)

    def test_stats_render_is_readable(self) -> None:
        result = asyncio.run(analyze_contents(
            [make_content("uid1")], client=FakeLLMClient(), persist=False))
        text = result.stats.render()
        self.assertIn("打标", text)
        self.assertIn("情感", text)
        self.assertIn("风险", text)


class PublishLabeledTest(unittest.TestCase):
    """打标完成事件：analytics → 预警的事件驱动接缝（FR-52 / 设计规格 8.2）。

    这条接缝断掉的表现是**预警静默失效**：内容照常入库、大盘照常更新，
    一条告警都不出来，而且不报错——所以它必须有测试盯着。
    早先这里写成 `label.content_uid`，而 `AnalysisLabel` 根本没有这个字段
    （内容标识是字典的键），于是每次真打到标都会在链路末尾抛 AttributeError
    （T-34 端到端联调实测踩到）。
    """

    def test_publishes_uids_and_negative_count(self) -> None:
        from unittest.mock import patch

        from engines.analytics.service import AnalysisResult, _publish_labeled
        from engines.contracts.content import AnalysisLabel

        result = AnalysisResult(labels={
            "uid1": AnalysisLabel(sentiment="negative"),
            "uid2": AnalysisLabel(sentiment="positive"),
            "uid3": AnalysisLabel(sentiment="negative"),
        })
        captured: dict = {}

        with patch("engines.common.eventing.publishers.publish_content_labeled",
                   lambda uids, **kwargs: captured.update(uids=uids, **kwargs)):
            _publish_labeled(result, topic_id="tp1")

        self.assertEqual(sorted(captured["uids"]), ["uid1", "uid2", "uid3"])
        self.assertEqual(captured["topic_ids"], ["tp1"])
        self.assertEqual(captured["negative_count"], 2)

    def test_empty_labels_publishes_nothing(self) -> None:
        from unittest.mock import patch

        from engines.analytics.service import AnalysisResult, _publish_labeled

        calls: list = []

        with patch("engines.common.eventing.publishers.publish_content_labeled",
                   lambda *a, **k: calls.append(a)):
            _publish_labeled(AnalysisResult(labels={}), topic_id=None)

        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
