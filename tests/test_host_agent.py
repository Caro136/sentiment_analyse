"""主持人单元测试。

重点是 **PairStore 的配对语义**：什么时候算"到齐"、重复推送怎么处理、
低质量章节会不会覆盖高质量章节。这三件事任何一个错了，
主持人都要么不研判、要么重复研判、要么拿次品材料做裁判。
"""

import unittest

from engines.contracts.dimensions import get_dimensions
from engines.contracts.section import SectionDraft
from engines.host_agent.nodes import (
    _render_final_report,
    route_after_find,
    route_after_record,
)
from engines.host_agent.pair_store import REQUIRED_SOURCES, PairStore, _quality_score
from engines.host_agent.schemas import FinalReportOutput
from engines.host_agent.state import HostSession, SectionJudgement


def draft(section_key: str, *, strength: str = "medium", count: int = 10,
          body: str = "正文内容", title: str = "") -> SectionDraft:
    return SectionDraft(section_key=section_key, index=0,
                        title=title or section_key, body=body,
                        strength=strength, evidence_count=count)


class PairStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = PairStore()

    def test_not_ready_with_single_source(self) -> None:
        """只有一侧到达时不该研判——否则拿单边材料冒充双方共识。"""
        self.store.add("insight", draft("heat_and_spread"))
        self.assertFalse(self.store.is_ready("heat_and_spread"))
        self.assertEqual(self.store.missing_sources("heat_and_spread"), ["media"])

    def test_ready_when_both_sources_arrive(self) -> None:
        self.store.add("insight", draft("heat_and_spread"))
        self.store.add("media", draft("heat_and_spread"))
        self.assertTrue(self.store.is_ready("heat_and_spread"))
        self.assertIn("heat_and_spread", self.store.ready_keys())

    def test_judged_key_not_ready_again(self) -> None:
        """研判过的维度不能再触发——否则两侧任意一侧重推就会重复研判。"""
        self.store.add("insight", draft("heat_and_spread"))
        self.store.add("media", draft("heat_and_spread"))
        self.store.mark_judged("heat_and_spread")
        self.assertFalse(self.store.is_ready("heat_and_spread"))
        self.assertNotIn("heat_and_spread", self.store.ready_keys())

    def test_duplicate_push_keeps_better_quality(self) -> None:
        """同一来源重推时保留质量更高的那份。"""
        self.store.add("insight", draft("heat_and_spread", strength="weak",
                                        count=2, body="短"))
        replaced = self.store.add("insight", draft("heat_and_spread", strength="strong",
                                                   count=30, body="长正文"))
        self.assertTrue(replaced)
        kept = self.store.pair_of("heat_and_spread")["insight"]
        self.assertEqual(kept.strength, "strong")
        self.assertEqual(kept.evidence_count, 30)

    def test_lower_quality_push_rejected(self) -> None:
        """低质量结果不该覆盖已有的好结果。"""
        self.store.add("insight", draft("heat_and_spread", strength="strong", count=30))
        replaced = self.store.add("insight", draft("heat_and_spread", strength="weak", count=1))
        self.assertFalse(replaced)
        self.assertEqual(self.store.pair_of("heat_and_spread")["insight"].strength, "strong")

    def test_ready_keys_follow_fixed_order(self) -> None:
        """就绪的维度按五维固定顺序返回，不随到达顺序变化。"""
        for key in ("deep_causes_and_impact", "background_overview"):
            self.store.add("insight", draft(key))
            self.store.add("media", draft(key))
        self.assertEqual(self.store.ready_keys(),
                         ["background_overview", "deep_causes_and_impact"])

    def test_all_done_requires_every_dimension(self) -> None:
        keys = [d.key for d in get_dimensions()]
        self.assertFalse(self.store.all_done())
        for key in keys[:-1]:
            self.store.mark_judged(key)
        self.assertFalse(self.store.all_done())
        self.store.mark_judged(keys[-1])
        self.assertTrue(self.store.all_done())

    def test_clear_resets_everything(self) -> None:
        """跨主题必须清空——配对按 section_key 做，残留会让新主题错配。"""
        self.store.add("insight", draft("heat_and_spread"))
        self.store.mark_judged("heat_and_spread")
        self.store.query = "旧主题"
        self.store.clear()
        self.assertEqual(self.store.results, {})
        self.assertEqual(self.store.judged, set())
        self.assertEqual(self.store.query, "")

    def test_stats_snapshot(self) -> None:
        self.store.add("insight", draft("background_overview"))
        stats = self.store.stats()
        self.assertIn("background_overview", stats["received"])
        self.assertFalse(stats["all_done"])


class QualityScoreTest(unittest.TestCase):
    def test_strength_outweighs_length(self) -> None:
        """证据强度优先于正文长度：长篇但只基于 2 条材料，不如短篇基于 30 条。"""
        strong_short = _quality_score(draft("k", strength="strong", count=30, body="短"))
        weak_long = _quality_score(draft("k", strength="weak", count=2, body="很长" * 500))
        self.assertGreater(strong_short, weak_long)

    def test_count_breaks_ties(self) -> None:
        more = _quality_score(draft("k", strength="medium", count=20))
        fewer = _quality_score(draft("k", strength="medium", count=5))
        self.assertGreater(more, fewer)

    def test_unknown_strength_treated_as_lowest(self) -> None:
        self.assertEqual(_quality_score(draft("k", strength="")), (0, 10, 4))


class RouteTest(unittest.TestCase):
    def test_judge_when_keys_ready(self) -> None:
        self.assertEqual(route_after_find({"ready_keys": ["a"]}), "judge")

    def test_end_when_nothing_ready(self) -> None:
        self.assertEqual(route_after_find({"ready_keys": []}), "end")

    def test_final_only_when_all_done(self) -> None:
        store = PairStore()
        self.assertEqual(route_after_record({"pairs": store}), "end")
        for dimension in get_dimensions():
            store.mark_judged(dimension.key)
        self.assertEqual(route_after_record({"pairs": store}), "final")

    def test_missing_pairs_goes_to_end(self) -> None:
        self.assertEqual(route_after_record({}), "end")


class HostSessionTest(unittest.TestCase):
    def test_round_trip_state(self) -> None:
        """会话与图状态的双向投影必须无损。"""
        session = HostSession(query="星耀XPhone15")
        session.judgements.append(SectionJudgement(
            section_key="heat_and_spread", title="热度", summary="讨论集中"))

        state = session.to_state(draft("heat_and_spread"), "insight")
        self.assertEqual(state["query"], "星耀XPhone15")
        self.assertEqual(state["source"], "insight")
        self.assertEqual(state["incoming_section_key"], "heat_and_spread")

        state["judgements"].append(SectionJudgement(
            section_key="sentiment_and_opinion", title="情感", summary="负面为主"))
        session.apply_state(state)
        self.assertEqual(len(session.judgements), 2)

    def test_ordered_judgements_by_dimension(self) -> None:
        session = HostSession()
        session.judgements = [
            SectionJudgement("deep_causes_and_impact", "影响"),
            SectionJudgement("background_overview", "背景"),
        ]
        self.assertEqual([j.section_key for j in session.ordered_judgements()],
                         ["background_overview", "deep_causes_and_impact"])

    def test_is_complete_requires_report(self) -> None:
        session = HostSession()
        for dimension in get_dimensions():
            session.pairs.mark_judged(dimension.key)
        self.assertFalse(session.is_complete)      # 还没最终裁判
        session.final_report = "结论"
        self.assertTrue(session.is_complete)

    def test_clear_resets_session(self) -> None:
        session = HostSession(query="x")
        session.judgements.append(SectionJudgement("heat_and_spread"))
        session.final_report = "结论"
        session.clear()
        self.assertEqual(session.judgements, [])
        self.assertEqual(session.final_report, "")
        self.assertEqual(session.query, "")


class JudgementRenderTest(unittest.TestCase):
    def test_markdown_contains_sections(self) -> None:
        judgement = SectionJudgement(
            section_key="heat_and_spread", title="舆情热度与传播",
            consensus=["两边都认为传播由 KOL 引爆"],
            divergence=["私域称已回落，公域称仍在扩散"],
            risks=["缺少互动量数据"],
            summary="传播已进入平台期")
        markdown = judgement.to_markdown()
        self.assertIn("舆情热度与传播", markdown)
        self.assertIn("共识", markdown)
        self.assertIn("分歧", markdown)
        self.assertIn("风险与缺口", markdown)

    def test_empty_sections_omitted(self) -> None:
        judgement = SectionJudgement(section_key="k", title="标题", summary="只有结论")
        markdown = judgement.to_markdown()
        self.assertNotIn("共识", markdown)
        self.assertNotIn("分歧", markdown)

    def test_final_report_render(self) -> None:
        output = FinalReportOutput(
            conclusion="整体处于扩散期", key_risks=["质量争议升级"],
            recommendations=["24 小时内发布说明"], confidence=0.75)
        report = _render_final_report("星耀XPhone15", output)
        self.assertIn("综合研判结论", report)
        self.assertIn("优先风险", report)
        self.assertIn("行动建议", report)
        self.assertIn("75%", report)


class RequiredSourcesTest(unittest.TestCase):
    def test_two_sources_configured(self) -> None:
        self.assertEqual(set(REQUIRED_SOURCES), {"insight", "media"})


if __name__ == "__main__":
    unittest.main()
