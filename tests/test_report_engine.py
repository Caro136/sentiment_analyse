"""报告引擎测试。

渲染器是纯函数，重点覆盖；引擎的材料缺失判定与统计格式化用替身验证。
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from engines.report_engine.engine import (
    ReportOutput,
    _format_stats,
    _truncate,
    generate_report,
)
from engines.report_engine.loader import ReportInputs
from engines.report_engine.renderer import markdown_to_html_body, render_html


class RendererTest(unittest.TestCase):
    def test_renders_headings_and_lists(self) -> None:
        body = markdown_to_html_body("# 标题\n\n- 项目一\n- 项目二")
        self.assertIn("<h1", body)
        self.assertIn("<li>项目一</li>", body)

    def test_renders_table(self) -> None:
        markdown = "| 列A | 列B |\n|---|---|\n| 1 | 2 |"
        body = markdown_to_html_body(markdown)
        self.assertIn("<table>", body)
        self.assertIn("<td>1</td>", body)

    def test_html_is_self_contained(self) -> None:
        """样式必须内联——报告会被转发归档，依赖外链样式换台机器就是裸文本。"""
        page = render_html("# 标题", "测试报告")
        self.assertIn("<style>", page)
        self.assertNotIn("<link", page)          # 不引外部样式
        self.assertNotIn("http://", page.split("<body>")[0])
        self.assertIn("<!DOCTYPE html>", page)

    def test_html_has_print_support(self) -> None:
        """D-07 定的浏览器打印方案要求页面自带打印样式。"""
        page = render_html("# 标题", "测试报告")
        self.assertIn("@media print", page)

    def test_meta_block_rendered(self) -> None:
        page = render_html("# 标题", "报告", meta={"研判主题": "某品牌", "信源数": 4})
        self.assertIn("研判主题：某品牌", page)
        self.assertIn("信源数：4", page)

    def test_meta_escapes_html(self) -> None:
        """主题词可能含尖括号，不转义会破坏页面结构。"""
        page = render_html("# 标题", "报告", meta={"主题": "<script>x</script>"})
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_empty_markdown(self) -> None:
        page = render_html("", "空报告")
        self.assertIn("<!DOCTYPE html>", page)


class StatsFormatTest(unittest.TestCase):
    def test_formats_core_metrics(self) -> None:
        text = _format_stats({
            "total_volume": 2825, "negative_ratio": 0.4284,
            "sentiment": {"negative": 1210, "neutral": 1102, "positive": 513},
        })
        self.assertIn("2825", text)
        self.assertIn("42.8%", text)
        self.assertIn("negative 1210", text)

    def test_formats_risk_levels_in_order(self) -> None:
        text = _format_stats({"risk_levels": {"C": 10, "S": 2, "A": 5, "B": 3}})
        self.assertLess(text.index("S 级"), text.index("A 级"))
        self.assertLess(text.index("A 级"), text.index("B 级"))

    def test_empty_stats(self) -> None:
        self.assertIn("无统计数据", _format_stats({}))

    def test_error_surfaced(self) -> None:
        """统计失败要写进材料里——否则模型会以为真的没有数据。"""
        text = _format_stats({"error": "OperationalError"})
        self.assertIn("OperationalError", text)


class TruncateTest(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        self.assertEqual(_truncate("短文本", 100), "短文本")

    def test_long_text_marked(self) -> None:
        result = _truncate("很长的内容" * 100, limit=50)
        self.assertIn("已截断", result)
        self.assertLess(len(result), 200)


class ReportInputsTest(unittest.TestCase):
    def test_ready_when_all_present(self) -> None:
        inputs = ReportInputs(query="x", host_report="h", insight_report="i",
                              media_report="m")
        self.assertTrue(inputs.is_ready)
        self.assertEqual(inputs.describe_missing(), "")

    def test_not_ready_reports_which_missing(self) -> None:
        inputs = ReportInputs(query="x", host_report="h", media_report="m")
        inputs.missing.append("insight")
        self.assertFalse(inputs.is_ready)
        self.assertIn("私域分析", inputs.describe_missing())

    def test_all_missing_described(self) -> None:
        inputs = ReportInputs(query="x", missing=["host", "insight", "media"])
        description = inputs.describe_missing()
        for name in ("主持人研判", "私域分析", "公域分析"):
            self.assertIn(name, description)


class FakeClient:
    def __init__(self, text: str = "## 一、事件基本信息\n\n正文", error: Exception | None = None):
        self._text = text
        self._error = error
        self.calls = 0

    async def generate_text(self, prompt, system=None, **kwargs):
        self.calls += 1
        if self._error:
            raise self._error
        return self._text


class GenerateReportTest(unittest.TestCase):
    def _inputs(self) -> ReportInputs:
        return ReportInputs(query="测试主题", host_report="主持人结论",
                            insight_report="私域内容", media_report="公域内容",
                            stats={"total_volume": 100, "negative_ratio": 0.4})

    def test_rejects_when_materials_missing(self) -> None:
        """**核心约束**：材料不全要拒绝生成并说清缺什么，而不是产出半成品。"""
        import asyncio

        inputs = ReportInputs(query="测试主题", host_report="主持人结论")
        inputs.missing = ["insight", "media"]
        result = asyncio.run(generate_report("测试主题", inputs=inputs,
                                             client=FakeClient(), persist=False))
        self.assertFalse(result.ok)
        self.assertIn("私域分析", result.error)
        self.assertIn("公域分析", result.error)
        self.assertEqual(result.missing, ["insight", "media"])

    def test_generates_and_renders(self) -> None:
        import asyncio

        client = FakeClient("## 一、事件基本信息\n\n某品牌舆情 100 条")
        result = asyncio.run(generate_report("测试主题", inputs=self._inputs(),
                                             client=client, persist=False))
        self.assertTrue(result.ok)
        self.assertEqual(client.calls, 1)
        self.assertIn("事件基本信息", result.markdown)
        self.assertIn("<!DOCTYPE html>", result.html)

    def test_model_failure_recorded(self) -> None:
        import asyncio

        result = asyncio.run(generate_report(
            "测试主题", inputs=self._inputs(),
            client=FakeClient(error=RuntimeError("模型超时")), persist=False))
        self.assertFalse(result.ok)
        self.assertIn("RuntimeError", result.error)

    def test_persists_both_formats(self) -> None:
        import asyncio

        with TemporaryDirectory() as tmp:
            result = asyncio.run(generate_report(
                "测试主题", inputs=self._inputs(), client=FakeClient(),
                output_dir=Path(tmp)))
            self.assertTrue(result.ok)
            markdown_file = Path(result.markdown_path)
            html_file = Path(result.html_path)
            self.assertTrue(markdown_file.exists())
            self.assertTrue(html_file.exists())
            self.assertIn("事件基本信息", markdown_file.read_text(encoding="utf-8"))
            self.assertIn("<!DOCTYPE html>", html_file.read_text(encoding="utf-8"))

    def test_stats_passed_through(self) -> None:
        import asyncio

        result = asyncio.run(generate_report("测试主题", inputs=self._inputs(),
                                             client=FakeClient(), persist=False))
        self.assertEqual(result.stats["total_volume"], 100)

    def test_output_render_readable(self) -> None:
        import asyncio

        result = asyncio.run(generate_report("测试主题", inputs=self._inputs(),
                                             client=FakeClient(), persist=False))
        self.assertIn("报告已生成", result.render())

    def test_failed_render_readable(self) -> None:
        output = ReportOutput(query="x", error="材料不全")
        self.assertIn("材料不全", output.render())

    def test_to_dict_shape(self) -> None:
        output = ReportOutput(query="x", markdown="内容")
        payload = output.to_dict()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["query"], "x")


if __name__ == "__main__":
    unittest.main()
