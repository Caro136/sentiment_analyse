"""私域研究员的单元测试。

图本身依赖数据库与模型，用真实运行验证；这里覆盖纯逻辑部分：
章节规整（模型输出不可信）、条件路由、草稿渲染。
"""

import unittest

from engines.insight_agent.reporting import normalize_plan, route_after_summarize
from engines.insight_agent.schemas import ResearchPlanOutput, SectionSpec
from engines.insight_agent.state import ResearchPlan, SectionDraft


class SectionDraftTest(unittest.TestCase):
    def test_to_markdown_contains_heading_and_body(self) -> None:
        draft = SectionDraft("heat_and_spread", 1, "舆情热度与传播", "正文内容")
        markdown = draft.to_markdown()
        self.assertIn("## 舆情热度与传播", markdown)
        self.assertIn("正文内容", markdown)

    def test_fallback_defaults_false(self) -> None:
        self.assertFalse(SectionDraft("k", 0, "标题").is_fallback)


class ResearchPlanTest(unittest.TestCase):
    def _plan(self) -> ResearchPlan:
        return ResearchPlan(overview="概览", sections=[
            ("background_overview", "事件背景", ["起因是什么"]),
            ("heat_and_spread", "热度传播", []),
        ])

    def test_keys_in_order(self) -> None:
        self.assertEqual(self._plan().keys,
                         ["background_overview", "heat_and_spread"])

    def test_title_lookup(self) -> None:
        self.assertEqual(self._plan().title_of("heat_and_spread"), "热度传播")

    def test_title_of_unknown_falls_back_to_key(self) -> None:
        self.assertEqual(self._plan().title_of("not_a_key"), "not_a_key")

    def test_focus_lookup(self) -> None:
        self.assertEqual(self._plan().focus_of("background_overview"), ["起因是什么"])

    def test_focus_of_unknown_is_empty(self) -> None:
        self.assertEqual(self._plan().focus_of("not_a_key"), [])


class NormalizePlanTest(unittest.TestCase):
    """章节规整是两侧对齐的基础，必须逐项验证。"""

    def test_fills_missing_dimensions(self) -> None:
        """模型只给了两章，其余三维必须补齐——缺章会让主持人漏配对。"""
        output = ResearchPlanOutput(sections=[
            SectionSpec(section_key="heat_and_spread", title="热度"),
        ])
        plan = normalize_plan(output)
        self.assertEqual(len(plan.sections), 5)
        self.assertIn("background_overview", plan.keys)
        self.assertIn("deep_causes_and_impact", plan.keys)

    def test_order_is_fixed(self) -> None:
        """模型给的顺序不可信，必须回到五维固定序。"""
        output = ResearchPlanOutput(sections=[
            SectionSpec(section_key="deep_causes_and_impact", title="影响"),
            SectionSpec(section_key="background_overview", title="背景"),
        ])
        plan = normalize_plan(output)
        self.assertEqual(plan.keys[0], "background_overview")
        self.assertEqual(plan.keys[-1], "deep_causes_and_impact")

    def test_drops_unknown_section_keys(self) -> None:
        """模型可能臆造章节，直接丢弃而不是让它混进报告。"""
        output = ResearchPlanOutput(sections=[
            SectionSpec(section_key="hallucinated_section", title="瞎编的"),
            SectionSpec(section_key="heat_and_spread", title="热度"),
        ])
        plan = normalize_plan(output)
        self.assertNotIn("hallucinated_section", plan.keys)
        self.assertEqual(len(plan.sections), 5)

    def test_duplicate_keys_handled(self) -> None:
        output = ResearchPlanOutput(sections=[
            SectionSpec(section_key="heat_and_spread", title="第一个"),
            SectionSpec(section_key="heat_and_spread", title="第二个"),
        ])
        plan = normalize_plan(output)
        self.assertEqual(len(plan.sections), 5)
        self.assertEqual(plan.title_of("heat_and_spread"), "第二个")

    def test_defaults_title_when_model_omits_it(self) -> None:
        output = ResearchPlanOutput(sections=[
            SectionSpec(section_key="heat_and_spread", title=""),
        ])
        plan = normalize_plan(output)
        self.assertEqual(plan.title_of("heat_and_spread"), "舆情热度与传播")

    def test_empty_model_output_yields_full_outline(self) -> None:
        """模型完全没给章节时，仍要产出完整五维大纲。"""
        plan = normalize_plan(ResearchPlanOutput())
        self.assertEqual(len(plan.sections), 5)

    def test_overview_preserved(self) -> None:
        plan = normalize_plan(ResearchPlanOutput(overview="数据支撑得起传播分析"))
        self.assertEqual(plan.overview, "数据支撑得起传播分析")

    def test_focus_points_preserved(self) -> None:
        output = ResearchPlanOutput(sections=[
            SectionSpec(section_key="sentiment_and_opinion", title="情感",
                        focus_points=["主流情绪是什么", "争议焦点在哪"]),
        ])
        plan = normalize_plan(output)
        self.assertEqual(plan.focus_of("sentiment_and_opinion"),
                         ["主流情绪是什么", "争议焦点在哪"])


class RouteTest(unittest.TestCase):
    def test_continues_while_sections_remain(self) -> None:
        state = {"plan": ResearchPlan(sections=[("a", "A", []), ("b", "B", [])]),
                 "cursor": 1}
        self.assertEqual(route_after_summarize(state), "summarize")

    def test_formats_when_all_done(self) -> None:
        state = {"plan": ResearchPlan(sections=[("a", "A", [])]), "cursor": 1}
        self.assertEqual(route_after_summarize(state), "format")

    def test_without_plan_goes_to_format(self) -> None:
        """规划缺失时不该卡在循环里，直接去排版（会输出空报告但有明确出口）。"""
        self.assertEqual(route_after_summarize({}), "format")


if __name__ == "__main__":
    unittest.main()
