"""报告产物补登记的测试（纯函数与文件扫描部分）。

**为什么不测写库那一段**：它只有「读目录 → 建记录 → 写文件行」三步，
真正的坑在判重与命名解析上——实测踩过「按完整路径比对导致每次都补一遍」，
因此这里把判重规则与命名解析钉死；写库路径由真机执行验证（幂等：连跑两次第二次全 skip）。
"""

import tempfile
import unittest
from pathlib import Path

from app.services.report.backfill import _normalize, parse_report_name, scanned_report

VALID = "report_新能源车购置税减免_1792044874.md"


class ParseNameTest(unittest.TestCase):
    def test_valid_name(self) -> None:
        self.assertEqual(parse_report_name(VALID), ("新能源车购置税减免", 1792044874))

    def test_invalid_names_return_none(self) -> None:
        for name in ("insight_星耀_123.md", "report_无时间戳.md",
                     "report_主题_不是时间.md", "report_主题.md", "报告.md"):
            with self.subTest(name=name):
                self.assertIsNone(parse_report_name(name))

    def test_timestamp_not_confused_with_topic_digits(self) -> None:
        """主题里带数字时，时间戳要从**末尾**取。"""
        parsed = parse_report_name("report_XPhone15发热_1792044874.md")
        self.assertEqual(parsed, ("XPhone15发热", 1792044874))

    def test_html_suffix_ignored(self) -> None:
        """只扫 .md（html 是它的兄弟文件），因此 .html 不该被解析成候选。"""
        self.assertIsNone(parse_report_name("report_主题_1792044874.html"))


class NormalizeTest(unittest.TestCase):
    def test_takes_basename_so_relative_and_absolute_match(self) -> None:
        """判重必须忽略目录差异。

        库里存的是相对项目根的路径（引擎落盘时写入），扫描得到的是绝对路径——
        按完整路径比会把「已登记」判成「未登记」，每次执行都补一遍，报告中心出现重复。
        """
        relative = "docs\\reports\\report_主题_1.md"
        absolute = "F:/Develop/workspace/wh260713/sentiment_analyse/docs/reports/report_主题_1.md"

        self.assertEqual(_normalize(relative), _normalize(absolute))

    def test_case_insensitive(self) -> None:
        self.assertEqual(_normalize("docs/R1.MD"), _normalize("x/r1.md"))


class ScanDirectoryTest(unittest.TestCase):
    def test_scan_pairs_html_and_sorts_by_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # 时间戳用真实的 10 位格式：解析规则要求至少 9 位数字
            for name in ("report_A_1792044100.md", "report_B_1792044300.md",
                         "report_C_1792044200.md"):
                (root / name).write_text(f"# {name}", encoding="utf-8")
            (root / "report_A_1792044100.html").write_text("<html>A</html>",
                                                          encoding="utf-8")
            (root / "insight_A_1792044100.md").write_text("角色产物，不是报告",
                                                         encoding="utf-8")
            (root / "report_坏名字.md").write_text("命名不符", encoding="utf-8")

            found = scanned_report(root)

        self.assertEqual([item.query for item in found], ["A", "C", "B"])   # 按时间正序
        self.assertEqual([item.generated_at for item in found],
                         [1792044100, 1792044200, 1792044300])
        self.assertIsNotNone(found[0].html_path)          # 有兄弟 html
        self.assertIsNone(found[1].html_path)             # 没有就是 None
        self.assertTrue(all(item.size_bytes > 0 for item in found))

    def test_missing_directory_returns_empty(self) -> None:
        self.assertEqual(scanned_report(Path(tempfile.gettempdir()) / "不存在的目录"), [])


if __name__ == "__main__":
    unittest.main()
