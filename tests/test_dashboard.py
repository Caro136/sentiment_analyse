"""大盘指标计算与物化测试（FR-36~FR-41）。

重点在三处「数字看起来正常但其实错了」的地方：

1. **空桶补 0**：折线图把没有数据的小时连起来，会让舆情看起来一直在高位；
2. **负面率无数据时为 null**，不是 0——0 会被读成「统计过了，全都不负面」；
3. **口径分层**：S/A 级内容数与 S/A 级事件数是两个指标，混成一个数就再也说不清。
"""

import unittest
from collections import Counter

from app.services.dashboard import metrics as m
from engines.contracts.enums import RiskLevel, Sentiment, SourceType

HOUR = 3600


def row(uid: str = "c1", *, sentiment: str = "negative", risk: str | None = "A",
        source: str = "social_media", at: int = 1792044000,
        topic: str | None = "tp1") -> m.MetricRow:
    return m.MetricRow(content_uid=uid, source_type=source, sentiment=sentiment,
                       risk_level=risk, topic_id=topic, analyzed_at=at)


class OverviewTest(unittest.TestCase):
    def test_counts_and_ratio(self) -> None:
        rows = [row("a"), row("b", sentiment="negative"),
                row("c", sentiment="neutral", risk="C"),
                row("d", sentiment="positive", risk="C")]
        overview = m.build_overview(rows)

        self.assertEqual(overview["total_volume"], 4)
        self.assertEqual(overview["negative_count"], 2)
        self.assertEqual(overview["negative_ratio"], 0.5)
        self.assertEqual(overview["high_risk_content_count"], 2)      # A + A
        self.assertEqual(overview["source_count"], 1)

    def test_empty_window_ratio_is_null_not_zero(self) -> None:
        """没有内容时负面率必须是 null——0 会被读成「统计过了，全都不负面」。"""
        overview = m.build_overview([])
        self.assertEqual(overview["total_volume"], 0)
        self.assertIsNone(overview["negative_ratio"])

    def test_alert_and_ticket_counts(self) -> None:
        alerts = [m.AlertRow(level="S", status="unread", event_id="e1"),
                  m.AlertRow(level="B", status="closed", event_id="e2"),
                  m.AlertRow(level="A", status="claimed", event_id="e1")]
        overview = m.build_overview([row()], alerts=alerts, open_tickets=2)

        self.assertEqual(overview["open_alert_count"], 2)       # closed 不计
        self.assertEqual(overview["high_level_alert_count"], 2)
        self.assertEqual(overview["open_ticket_count"], 2)

    def test_event_count_is_separate_metric(self) -> None:
        """S/A 级事件数与 S/A 级内容数是两个口径，分别给出。"""
        overview = m.build_overview([row("a"), row("b")],
                                    hot_event_ids={"evt_1"})
        self.assertEqual(overview["high_risk_content_count"], 2)
        self.assertEqual(overview["high_risk_event_count"], 1)

    def test_event_count_absent_is_null(self) -> None:
        """没传事件集合时给 null，而不是假装是 0（那会让故障看起来像「没有高危事件」）。"""
        self.assertIsNone(m.build_overview([row()])["high_risk_event_count"])

    def test_time_range(self) -> None:
        overview = m.build_overview([row("a", at=1000), row("b", at=5000)])
        self.assertEqual(overview["first_analyzed_at"], 1000)
        self.assertEqual(overview["last_analyzed_at"], 5000)


class VolumeTrendTest(unittest.TestCase):
    def test_buckets_by_hour(self) -> None:
        base = 1_792_044_000
        rows = [row("a", at=base), row("b", at=base + 60),
                row("c", at=base + HOUR, sentiment="neutral")]
        trend = m.build_volume_trend(rows, bucket="hour")

        self.assertEqual(len(trend["points"]), 2)
        self.assertEqual(trend["points"][0]["total"], 2)
        self.assertEqual(trend["points"][0]["negative"], 2)
        self.assertEqual(trend["points"][1]["total"], 1)
        self.assertEqual(trend["points"][1]["negative"], 0)

    def test_empty_buckets_are_filled_with_zero(self) -> None:
        """空桶必须补 0：直接连线会把「没数据的时段」视觉上抹平。"""
        start = 1_792_044_000 // HOUR * HOUR
        rows = [row("a", at=start), row("b", at=start + 3 * HOUR)]
        trend = m.build_volume_trend(rows, bucket="hour", start=start,
                                     end=start + 3 * HOUR)

        self.assertEqual(len(trend["points"]), 4)
        self.assertEqual([p["total"] for p in trend["points"]], [1, 0, 0, 1])

    def test_day_bucket(self) -> None:
        base = 1_792_044_000
        trend = m.build_volume_trend([row("a", at=base)], bucket="day")
        self.assertEqual(trend["bucket_seconds"], 24 * HOUR)
        self.assertEqual(len(trend["points"]), 1)

    def test_rows_without_time_ignored(self) -> None:
        trend = m.build_volume_trend([row("a", at=0)], bucket="hour")
        self.assertEqual(trend["points"], [])


class DistributionTest(unittest.TestCase):
    def test_sentiment_distribution(self) -> None:
        rows = [row("a", sentiment="negative"), row("b", sentiment="neutral"),
                row("c", sentiment="positive"), row("d", sentiment="positive")]
        distribution = m.build_sentiment_distribution(rows)

        self.assertEqual(distribution["total"], 4)
        self.assertEqual(distribution["counts"],
                         {"positive": 2, "neutral": 1, "negative": 1})
        self.assertEqual(distribution["ratios"]["positive"], 0.5)

    def test_all_sentiments_present_even_when_zero(self) -> None:
        """三类情感都要出现（哪怕为 0），否则前端环形图会缺一块。"""
        distribution = m.build_sentiment_distribution([row()])
        self.assertEqual(set(distribution["counts"]), {"positive", "neutral", "negative"})

    def test_source_distribution_order_is_fixed(self) -> None:
        rows = [row("a", source="short_video"), row("b", source="social_media")]
        distribution = m.build_source_distribution(rows)

        self.assertEqual([item["source_type"] for item in distribution["items"]],
                         [source.value for source in SourceType])
        by_type = {item["source_type"]: item for item in distribution["items"]}
        self.assertEqual(by_type["short_video"]["count"], 1)
        self.assertEqual(by_type["news_portal"]["count"], 0)


class HotWordsTest(unittest.TestCase):
    def test_entities_and_keywords_are_merged_by_weight(self) -> None:
        """权重可解释：实体 1.0×次数、词包词 1.2×次数，混在一起排序。

        星耀科技 10×1.0=10；电池 2×1.0=2；星耀XPhone15 1×1.2=1.2 → 按权重倒序。
        """
        hot = m.build_hot_words([], entity_mentions=Counter({"星耀科技": 10, "电池": 2}),
                               keyword_mentions=Counter({"星耀XPhone15": 1}))

        self.assertEqual([item["word"] for item in hot["items"]],
                         ["星耀科技", "电池", "星耀XPhone15"])
        self.assertEqual(hot["items"][0]["weight"], 10.0)
        self.assertEqual(hot["items"][2]["weight"], 1.2)

    def test_kind_marks_source(self) -> None:
        """词包词优先标注为 keyword——使用者明确关心它，看到热词时要知道它怎么来的。"""
        hot = m.build_hot_words([], entity_mentions=Counter({"散热争议": 5}),
                               keyword_mentions=Counter({"散热争议": 1}))
        self.assertEqual(hot["items"][0]["kind"], "keyword")

    def test_limit_applied(self) -> None:
        mentions = Counter({f"词{i}": i for i in range(1, 51)})
        hot = m.build_hot_words([], entity_mentions=mentions, limit=10)
        self.assertEqual(len(hot["items"]), 10)
        self.assertEqual(hot["items"][0]["word"], "词50")     # 权重倒序

    def test_empty_mentions(self) -> None:
        hot = m.build_hot_words([])
        self.assertEqual(hot["items"], [])
        self.assertEqual(hot["total_terms"], 0)

    def test_no_rerank_keywords_lower_weight(self) -> None:
        """权重可解释：词包词 1.2、实体 1.0，命中次数相同时词包词在前。"""
        hot = m.build_hot_words([], entity_mentions=Counter({"甲": 1}),
                               keyword_mentions=Counter({"乙": 1}))
        self.assertEqual([item["word"] for item in hot["items"]], ["乙", "甲"])


class MetricConsistencyTest(unittest.TestCase):
    def test_risk_levels_used_for_high_risk_count(self) -> None:
        rows = [row("a", risk=RiskLevel.S.value), row("b", risk=RiskLevel.A.value),
                row("c", risk=RiskLevel.B.value), row("d", risk=None)]
        overview = m.build_overview(rows)
        self.assertEqual(overview["high_risk_content_count"], 2)

    def test_sentiment_enum_values_match_counts_keys(self) -> None:
        """枚举取值与统计键必须一致，否则分布图会整块缺失（静默）。"""
        rows = [row("a", sentiment=sentiment.value) for sentiment in Sentiment]
        distribution = m.build_sentiment_distribution(rows)
        self.assertEqual(sum(distribution["counts"].values()), len(Sentiment))


if __name__ == "__main__":
    unittest.main()
