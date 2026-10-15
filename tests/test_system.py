"""系统运维测试（FR-72~FR-75）。

三处重点：

1. **任务埋点**：进入即 running、退出即 success/failed（异常也要留下 failed 痕迹）；
2. **日志检索**：只回读尾部、按条件过滤、**读出口脱敏**；
3. **重置的护栏**：确认口令、原始区拒绝、账号与审计永不触碰——这是全项目最危险的入口。
"""

import tempfile
import unittest
from pathlib import Path

from app.errors import AppError
from app.services.system import logs as log_service
from app.services.system import reset as reset_service


def make_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


LOG_LINE = ("{ts} | {level:<8} | {role} | abc123 | app.routers.x:handler:42 | {message}")


class LogSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_log(self.root / "app.log", [
            LOG_LINE.format(ts="2026-10-15 19:00:00.000", level="INFO", role="-",
                            message="启动完成"),
            LOG_LINE.format(ts="2026-10-15 19:05:00.000", level="WARNING", role="-",
                            message="报告任务等待中"),
            LOG_LINE.format(ts="2026-10-15 19:10:00.000", level="ERROR", role="report",
                            message="模型调用失败 api_key=sk-abcdefghijklmn"),
        ])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_matching_lines_in_time_order(self) -> None:
        result = log_service.search_logs(module="app", log_dir=self.root)
        self.assertEqual(result.matched, 3)
        self.assertIn("启动完成", result.lines[0].raw)         # 正序
        self.assertIn("模型调用失败", result.lines[-1].raw)
        self.assertEqual(result.lines[-1].level, "ERROR")

    def test_filter_by_level(self) -> None:
        result = log_service.search_logs(module="app", level="error", log_dir=self.root)
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.lines[0].level, "ERROR")

    def test_filter_by_keyword(self) -> None:
        result = log_service.search_logs(module="app", keyword="报告任务",
                                         log_dir=self.root)
        self.assertEqual(result.matched, 1)
        self.assertIn("报告任务", result.lines[0].raw)

    def test_filter_by_trace_id(self) -> None:
        result = log_service.search_logs(module="app", trace_id="abc123",
                                         log_dir=self.root)
        self.assertEqual(result.matched, 3)
        self.assertEqual(log_service.search_logs(module="app", trace_id="nope",
                                                 log_dir=self.root).matched, 0)

    def test_time_range_stops_reading(self) -> None:
        """时间范围是「越早越停」的剪枝：日志按时间正序写，早于起点就没必要再读。"""
        result = log_service.search_logs(module="app", start_time="2026-10-15 19:05:00",
                                         log_dir=self.root)
        self.assertEqual(result.matched, 2)
        self.assertNotIn("启动完成", "".join(line.raw for line in result.lines))

    def test_secrets_are_masked_on_read(self) -> None:
        """第三方库打印的内容不受写入脱敏控制，读出口必须再兜一道。"""
        result = log_service.search_logs(module="app", keyword="模型调用", log_dir=self.root)
        self.assertNotIn("sk-abcdefghijklmn", result.lines[0].raw)

    def test_limit_truncates_and_flags(self) -> None:
        result = log_service.search_logs(module="app", limit=1, log_dir=self.root)
        self.assertEqual(result.matched, 1)
        self.assertTrue(result.truncated)
        self.assertIn("模型调用失败", result.lines[0].raw)      # 取最近一条

    def test_missing_module_returns_empty(self) -> None:
        result = log_service.search_logs(module="不存在", log_dir=self.root)
        self.assertEqual(result.matched, 0)
        self.assertEqual(result.lines, [])

    def test_multi_line_entries_do_not_break(self) -> None:
        """异常堆栈是多行的：后续行没有时间戳，应作为原文保留而不是被丢弃。"""
        make_log(self.root / "app.log", [
            LOG_LINE.format(ts="2026-10-15 19:00:00.000", level="ERROR", role="-",
                            message="未捕获异常"),
            "Traceback (most recent call last):",
            "  File \"x.py\", line 1, in <module>",
        ])
        result = log_service.search_logs(module="app", log_dir=self.root)
        self.assertEqual(result.matched, 3)
        self.assertIn("Traceback", result.lines[1].raw)

    def test_module_listing(self) -> None:
        modules = {item["module"] for item in log_service.available_modules()}
        self.assertIn("app", modules)
        self.assertIn("report", modules)          # 角色日志也在可检索范围内


class ResetGuardTest(unittest.IsolatedAsyncioTestCase):
    """重置的护栏——这是全项目最危险的入口，测试重点全在「不许做什么」。"""

    def test_raw_scope_rejected(self) -> None:
        """原始区只读（ADR-03）：重置原始数据请重新生成数据集。"""
        with self.assertRaises(AppError) as ctx:
            reset_service._tables_for("raw")
        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("原始区", ctx.exception.message)
        self.assertIn("generate.py", ctx.exception.detail)

    def test_unknown_scope_rejected(self) -> None:
        with self.assertRaises(AppError):
            reset_service._tables_for("everything")

    def test_protected_tables_never_in_any_scope(self) -> None:
        """账号与审计永远不在删除名单里——清了账号就登不进来、也查不到谁干的。"""
        protected = set(reset_service.PROTECTED_TABLES)
        for scope in (*reset_service.RESET_SCOPES, "all"):
            tables = set(reset_service._tables_for(scope))
            with self.subTest(scope=scope):
                self.assertFalse(tables & protected,
                                 f"{scope} 试图删除受保护的表：{tables & protected}")

    def test_all_scope_covers_every_scope(self) -> None:
        tables = set(reset_service._tables_for("all"))
        for scope, scoped in reset_service.RESET_SCOPES.items():
            with self.subTest(scope=scope):
                self.assertTrue(set(scoped) <= tables)

    def test_confirmation_token_required(self) -> None:
        with self.assertRaises(AppError) as ctx:
            reset_service._check_confirmation("analysis", "")
        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("RESET_ANALYSIS", ctx.exception.message)

    def test_wrong_token_rejected(self) -> None:
        with self.assertRaises(AppError):
            reset_service._check_confirmation("analysis", "RESET_ALL")

    def test_correct_token_accepted(self) -> None:
        reset_service._check_confirmation("analysis", "RESET_ANALYSIS")
        reset_service._check_confirmation("all", "RESET_ALL")

    def test_delete_order_puts_child_tables_first(self) -> None:
        """先删从表再删主表：顺序反了在开启外键的库上会直接失败。"""
        tables = reset_service._tables_for("analysis")
        self.assertLess(tables.index("cluster_member"), tables.index("opinion_cluster"))
        self.assertLess(tables.index("event_content"), tables.index("event"))


class TaskTrackTest(unittest.IsolatedAsyncioTestCase):
    """任务埋点：状态转移与失败留痕。"""

    async def asyncSetUp(self) -> None:
        self.started: list[dict] = []
        self.finished: list[dict] = []

        class FakeRepo:
            async def start(inner, *, task_id, task_type, input_summary):
                self.started.append({"task_id": task_id, "task_type": task_type,
                                     "input_summary": input_summary})

            async def finish(inner, task_id, *, status, error="", duration_ms=None):
                self.finished.append({"task_id": task_id, "status": status,
                                      "error": error, "duration_ms": duration_ms})

        self.repo = FakeRepo()

    async def test_success_path(self) -> None:
        from app.services.system.tasks import track_task

        async with track_task(self.repo, task_type="alert",
                              input_summary="window=60min") as task_id:
            self.assertTrue(task_id.startswith("task_alert_"))

        self.assertEqual(len(self.started), 1)
        self.assertEqual(self.started[0]["task_type"], "alert")
        self.assertEqual(self.finished[0]["status"], "success")

    async def test_duration_is_measured_in_milliseconds(self) -> None:
        """耗时由埋点自己用 perf_counter 量，不能靠秒级时间戳推算。

        推算的后果是亚秒任务一律显示 0ms——运维看板上会是一片「瞬时完成」，
        而一次预警扫描实际要几百毫秒。
        """
        import asyncio

        from app.services.system.tasks import track_task

        async with track_task(self.repo, task_type="alert"):
            await asyncio.sleep(0.05)

        measured = self.finished[0]["duration_ms"]
        self.assertIsNotNone(measured)
        self.assertGreaterEqual(measured, 40)      # 至少睡了 50ms，允许调度误差
        self.assertLess(measured, 2000)

    async def test_failure_is_recorded_and_reraised(self) -> None:
        from app.services.system.tasks import track_task

        with self.assertRaises(RuntimeError):
            async with track_task(self.repo, task_type="dashboard"):
                raise RuntimeError("模型炸了")

        self.assertEqual(self.finished[0]["status"], "failed")
        self.assertIn("RuntimeError", self.finished[0]["error"])

    async def test_custom_task_id_respected(self) -> None:
        from app.services.system.tasks import track_task

        async with track_task(self.repo, task_type="report", task_id="task_report_x"):
            pass

        self.assertEqual(self.finished[0]["task_id"], "task_report_x")


if __name__ == "__main__":
    unittest.main()
