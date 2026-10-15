"""契约层测试。

其中 `EnumContractTest` 是一组「契约冻结」测试：枚举字面值与数据生成器、
数据库 DDL 三处必须一致，谁改了一处而忘了另外两处，这里立刻失败。
这类错误的后果是链路静默错位——数据筛不出来，但不报错。
"""

import unittest

from engines.contracts.content import (
    RECORD_COMMENT,
    RECORD_POST,
    Author,
    Engagement,
    UnifiedContent,
    hotness_score,
    make_content_uid,
    parse_content_uid,
)
from engines.contracts.dimensions import (
    DIMENSIONS,
    dimension_keys,
    get_dimension,
    get_dimensions,
    normalize_section_keys,
)
from engines.contracts.enums import (
    EvidenceStrength,
    RiskLevel,
    Sentiment,
    SourceType,
)
from engines.contracts.evidence import (
    CHANNEL_COMMENT,
    CHANNEL_HOT,
    CHANNEL_KEYWORD,
    CHANNEL_QUOTAS,
    CHANNEL_SEMANTIC,
    CHANNEL_WEIGHTS,
    EvidenceCluster,
    EvidencePool,
    EvidenceRecord,
    RetrievalMeta,
    SectionEvidencePack,
    score_evidence,
)


class EnumContractTest(unittest.TestCase):
    """枚举取值已冻结。改动必须同步三处：本文件、data/generator、sql DDL。"""

    def test_source_type_values(self) -> None:
        self.assertEqual(SourceType.SOCIAL.value, "social_media")
        self.assertEqual(SourceType.VIDEO.value, "short_video")
        self.assertEqual(SourceType.NEWS.value, "news_portal")
        self.assertEqual(SourceType.FORUM.value, "industry_forum")

    def test_sentiment_values(self) -> None:
        self.assertEqual(Sentiment.POSITIVE.value, "positive")
        self.assertEqual(Sentiment.NEUTRAL.value, "neutral")
        self.assertEqual(Sentiment.NEGATIVE.value, "negative")

    def test_risk_level_severity_order(self) -> None:
        self.assertGreater(RiskLevel.S.severity, RiskLevel.A.severity)
        self.assertGreater(RiskLevel.A.severity, RiskLevel.B.severity)
        self.assertGreater(RiskLevel.B.severity, RiskLevel.C.severity)

    def test_source_labels_are_chinese(self) -> None:
        for source in SourceType:
            self.assertTrue(source.label)

    def test_evidence_strength_from_count(self) -> None:
        self.assertIs(EvidenceStrength.from_count(0), EvidenceStrength.NONE)
        self.assertIs(EvidenceStrength.from_count(3), EvidenceStrength.WEAK)
        self.assertIs(EvidenceStrength.from_count(6), EvidenceStrength.MEDIUM)
        self.assertIs(EvidenceStrength.from_count(20), EvidenceStrength.STRONG)


class DimensionTest(unittest.TestCase):
    def test_exactly_five_dimensions(self) -> None:
        self.assertEqual(len(get_dimensions()), 5)
        self.assertEqual(len(DIMENSIONS), 5)

    def test_order_is_fixed_and_contiguous(self) -> None:
        self.assertEqual([d.index for d in get_dimensions()], [0, 1, 2, 3, 4])

    def test_each_dimension_is_fully_defined(self) -> None:
        for dim in get_dimensions():
            with self.subTest(dimension=dim.key):
                self.assertTrue(dim.title)
                self.assertTrue(dim.insight_goal)
                self.assertTrue(dim.media_goal)
                self.assertTrue(dim.cluster_keywords)
                self.assertTrue(dim.report_section)

    def test_unknown_dimension_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_dimension("not_a_dimension")

    def test_normalize_fills_missing_dimensions(self) -> None:
        """章节规划由模型产出，缺章必须被兜底补齐，否则配对研判会漏维度。"""
        result = normalize_section_keys(["heat_and_spread"])
        self.assertEqual(len(result), 5)
        self.assertEqual(result, list(dimension_keys()))

    def test_normalize_drops_unknown_and_dedupes(self) -> None:
        result = normalize_section_keys(
            ["heat_and_spread", "hallucinated_key", "heat_and_spread"])
        self.assertEqual(len(result), 5)
        self.assertNotIn("hallucinated_key", result)

    def test_normalize_restores_correct_order(self) -> None:
        result = normalize_section_keys(
            ["deep_causes_and_impact", "background_overview", "sentiment_and_opinion"])
        self.assertEqual(result, list(dimension_keys()))

    def test_normalize_empty_input(self) -> None:
        self.assertEqual(normalize_section_keys([]), list(dimension_keys()))


class ContentUidTest(unittest.TestCase):
    def test_make_and_parse_roundtrip(self) -> None:
        uid = make_content_uid(SourceType.VIDEO, "douyin_aweme", 7648947265968639247)
        self.assertEqual(uid, "short_video:douyin_aweme:7648947265968639247")
        self.assertEqual(parse_content_uid(uid),
                         ("short_video", "douyin_aweme", 7648947265968639247))

    def test_accepts_plain_string_source(self) -> None:
        self.assertEqual(make_content_uid("news_portal", "news_article", 7),
                         "news_portal:news_article:7")

    def test_invalid_uid_raises(self) -> None:
        for bad in ("", "only_two:parts", "a:b:c:d"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    parse_content_uid(bad)


class EngagementTest(unittest.TestCase):
    def test_total_sums_all_dimensions(self) -> None:
        self.assertEqual(Engagement(likes=1, comments=2, shares=3,
                                    collects=4, replies=5).total, 15)

    def test_shares_outweigh_likes(self) -> None:
        """分享触达新人群，点赞只是个人态度——传播力不同。"""
        self.assertGreater(hotness_score(Engagement(shares=1)),
                           hotness_score(Engagement(likes=1)))

    def test_hotness_of_empty_is_zero(self) -> None:
        self.assertEqual(hotness_score(Engagement()), 0.0)


class AuthorTest(unittest.TestCase):
    def test_kol_threshold(self) -> None:
        self.assertTrue(Author(account_id="a", nickname="n", influence_score=80).is_kol)
        self.assertFalse(Author(account_id="a", nickname="n", influence_score=30).is_kol)

    def test_official_types(self) -> None:
        for verified in ("官方", "政务", "媒体"):
            with self.subTest(verified=verified):
                self.assertTrue(Author(account_id="a", nickname="n",
                                       verified_type=verified).is_official)
        self.assertFalse(Author(account_id="a", nickname="n",
                                verified_type="黄V").is_official)


class UnifiedContentTest(unittest.TestCase):
    def _make(self, **overrides) -> UnifiedContent:
        base = dict(
            content_uid="short_video:douyin_aweme:1",
            source_type=SourceType.VIDEO,
            source_table="douyin_aweme",
            business_id=1,
            text="正文内容",
        )
        base.update(overrides)
        return UnifiedContent(**base)

    def test_analysis_text_includes_modality(self) -> None:
        """图片里的文字往往正是最关键的信息，必须一并参与分析。"""
        content = self._make(modality_texts=("图中文字：售后服务电话",))
        self.assertIn("售后服务电话", content.analysis_text)
        self.assertIn("正文内容", content.analysis_text)

    def test_display_text_falls_back_to_title(self) -> None:
        self.assertEqual(self._make(text="", title="标题").display_text, "标题")

    def test_content_hash_is_stable(self) -> None:
        self.assertEqual(self._make().content_hash, self._make().content_hash)

    def test_content_hash_differs_for_different_text(self) -> None:
        self.assertNotEqual(self._make().content_hash,
                            self._make(text="另一段正文").content_hash)

    def test_record_type_default_is_post(self) -> None:
        self.assertEqual(self._make().record_type, RECORD_POST)
        self.assertEqual(self._make(record_type=RECORD_COMMENT).record_type, RECORD_COMMENT)


class ScoreEvidenceTest(unittest.TestCase):
    def _record(self, channel: str, score: float, hotness: float) -> EvidenceRecord:
        return EvidenceRecord(
            record_id="r1",
            content_uid="social_media:weibo_note:1",
            source_type=SourceType.SOCIAL,
            retrieval=RetrievalMeta(
                matched_queries=("q",),
                retrieval_channels=(channel,),
                retrieval_scores={channel: score},
            ),
            hotness_score=hotness,
        )

    def test_relevance_and_hotness_both_contribute(self) -> None:
        high_rel = score_evidence(self._record(CHANNEL_KEYWORD, 1.0, 0), 100)
        high_hot = score_evidence(self._record(CHANNEL_KEYWORD, 0.0, 100), 100)
        self.assertGreater(high_rel, 0)
        self.assertGreater(high_hot, 0)

    def test_semantic_channel_outweighs_hot_channel(self) -> None:
        """语义召回命中「表达不同但语义相近」的内容，是关键词召回漏掉的部分。"""
        semantic = score_evidence(self._record(CHANNEL_SEMANTIC, 1.0, 0), 100)
        hot = score_evidence(self._record(CHANNEL_HOT, 1.0, 0), 100)
        self.assertGreater(semantic, hot)

    def test_score_is_capped_at_one(self) -> None:
        record = self._record(CHANNEL_SEMANTIC, 1.0, 1_000_000)
        self.assertLessEqual(score_evidence(record, 1), 1.0)

    def test_zero_max_hotness_does_not_divide_by_zero(self) -> None:
        self.assertGreaterEqual(score_evidence(self._record(CHANNEL_KEYWORD, 0.5, 0), 0), 0)

    def test_channel_weights_and_quotas_cover_all_channels(self) -> None:
        for channel in (CHANNEL_KEYWORD, CHANNEL_COMMENT, CHANNEL_HOT, CHANNEL_SEMANTIC):
            with self.subTest(channel=channel):
                self.assertIn(channel, CHANNEL_WEIGHTS)
                self.assertIn(channel, CHANNEL_QUOTAS)


class EvidencePoolTest(unittest.TestCase):
    def _record(self, uid_suffix: str, cluster: str | None,
                source: SourceType = SourceType.SOCIAL) -> EvidenceRecord:
        return EvidenceRecord(
            record_id=f"r{uid_suffix}",
            content_uid=f"{source.value}:tbl:{uid_suffix}",
            source_type=source,
            cluster_id=cluster,
        )

    def test_by_dimension_uses_cluster_naming_convention(self) -> None:
        pool = EvidencePool(query="q", records=[
            self._record("1", "cluster_heat_and_spread"),
            self._record("2", "cluster_heat_and_spread"),
            self._record("3", "cluster_sentiment_and_opinion"),
        ])
        self.assertEqual(len(pool.by_dimension("heat_and_spread")), 2)
        self.assertEqual(len(pool.by_dimension("sentiment_and_opinion")), 1)
        self.assertEqual(len(pool.by_dimension("background_overview")), 0)

    def test_platform_distribution(self) -> None:
        pool = EvidencePool(query="q", records=[
            self._record("1", None, SourceType.SOCIAL),
            self._record("2", None, SourceType.SOCIAL),
            self._record("3", None, SourceType.VIDEO),
        ])
        dist = pool.platform_distribution()
        self.assertEqual(dist["social_media"], 2)
        self.assertEqual(dist["short_video"], 1)

    def test_len_reflects_records(self) -> None:
        pool = EvidencePool(query="q")
        self.assertEqual(len(pool), 0)
        pool.records.append(self._record("1", None))
        self.assertEqual(len(pool), 1)


class SectionEvidencePackTest(unittest.TestCase):
    def _records(self, count: int) -> list[EvidenceRecord]:
        return [
            EvidenceRecord(
                record_id=f"r{i}",
                content_uid=f"social_media:weibo_note:{i}",
                source_type=SourceType.SOCIAL,
                content=f"第 {i} 条内容",
                author_nickname=f"用户{i}",
                hotness_score=i * 10,
            )
            for i in range(count)
        ]

    def test_empty_pack_is_marked(self) -> None:
        pack = SectionEvidencePack.from_records("heat_and_spread", "q", [])
        self.assertTrue(pack.is_empty)
        self.assertFalse(pack.is_sufficient)

    def test_pack_strength_follows_count(self) -> None:
        self.assertTrue(SectionEvidencePack.from_records(
            "k", "q", self._records(12)).is_sufficient)

    def test_blocks_are_capped(self) -> None:
        """证据再多也不能无限塞进 Prompt——超出的部分对模型是噪声。"""
        pack = SectionEvidencePack.from_records("k", "q", self._records(100),
                                                max_blocks=30)
        self.assertEqual(len(pack.evidence_source_blocks), 30)
        self.assertEqual(pack.evidence_count, 100)   # 总数仍如实记录

    def test_block_contains_source_and_author(self) -> None:
        pack = SectionEvidencePack.from_records("k", "q", self._records(1))
        block = pack.evidence_source_blocks[0]
        self.assertIn("社交媒体", block)
        self.assertIn("用户0", block)


class ClusterTest(unittest.TestCase):
    def test_empty_cluster_flag(self) -> None:
        self.assertTrue(EvidenceCluster(cluster_id="c1").is_empty)
        self.assertFalse(EvidenceCluster(cluster_id="c1", size=3).is_empty)


if __name__ == "__main__":
    unittest.main()
