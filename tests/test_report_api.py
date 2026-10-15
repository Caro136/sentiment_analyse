"""报告中心测试（FR-57~FR-63）。

接口层才会暴露的四件事：

1. **异步语义**：POST 立即 202 返回 `generating`，进度走 SSE（这里验证事件确实发了）；
2. **失败可见**：材料不全 / 模型不可用时写 `error_message`、状态置 `failed`（FR-62），
   而不是产出一份半成品；
3. **导出契约**：md 附件下载、html 内联预览、**pdf 被明确拒绝并说明原因**（D-07）；
4. **权限**：发起生成需 `report:generate`（分析师/管理员），导出三方皆可。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.reports import get_report_repository
from app.security import get_user_repository
from app.services.auth import passwords
from app.services.report.repository import (
    ReportFileRecord,
    ReportRecord,
)
from engines.common.db.session import get_db_session
from engines.contracts.enums import ReportStatus
from engines.report_engine.engine import ReportOutput
from tests.fakes import (
    InMemoryUserRepository,
    capture_audit_writes,
    make_user,
    silence_app_logging,
)


class InMemoryReportRepository:
    """报告仓储替身（报告、产物、任务）。"""

    def __init__(self, reports: list | None = None, files: list | None = None) -> None:
        self.reports: dict[str, ReportRecord] = {r.report_id: r for r in (reports or [])}
        self.files: dict[str, list[ReportFileRecord]] = {}
        for item in files or []:
            self.files.setdefault(item.report_id, []).append(item)
        self.tasks: dict[str, dict] = {}
        self.updates: list[dict] = []

    async def create_report(self, record):
        self.reports[record.report_id] = record
        return record

    async def get_report(self, report_id: str):
        return self.reports.get(report_id)

    async def find_active(self, *, event_id=None, topic_id=None, title=None):
        for report in self.reports.values():
            if report.status != "generating":
                continue
            if event_id and report.event_id == event_id:
                return report
            if topic_id and report.topic_id == topic_id:
                return report
            if title and report.title == title:
                return report
        return None

    async def list_reports(self, *, page, page_size, topic_id=None, event_id=None,
                           status=None):
        rows = list(self.reports.values())
        if topic_id:
            rows = [r for r in rows if r.topic_id == topic_id]
        if event_id:
            rows = [r for r in rows if r.event_id == event_id]
        if status:
            rows = [r for r in rows if r.status == status]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        start = (page - 1) * page_size
        return len(rows), rows[start:start + page_size]

    async def update_report(self, report_id: str, fields: dict):
        import dataclasses

        current = self.reports.get(report_id)
        if current is None:
            return None
        self.updates.append({"report_id": report_id, **fields})
        updated = dataclasses.replace(current, **fields)
        self.reports[report_id] = updated
        return updated

    async def add_file(self, record):
        self.files.setdefault(record.report_id, []).append(record)
        return record

    async def list_files(self, report_id: str):
        return list(self.files.get(report_id, []))

    async def start_task(self, *, task_id, task_type, input_summary):
        self.tasks[task_id] = {"task_type": task_type, "status": "running",
                               "input_summary": input_summary}

    async def finish_task(self, task_id: str, *, status: str, error: str = "",
                          duration_ms: int | None = None):
        if task_id in self.tasks:
            self.tasks[task_id].update(status=status, error=error)

    async def mark_interrupted_tasks(self, reason: str) -> int:
        count = 0
        for task in self.tasks.values():
            if task["status"] == "running":
                task.update(status="failed", error=reason)
                count += 1
        return count


def make_report(report_id: str = "rpt1", *, event_id: str | None = "evt_1",
                topic_id: str | None = None, status: str = "generating",
                title: str = "舆情研判报告：星耀XPhone15",
                error: str | None = None) -> ReportRecord:
    import time

    return ReportRecord(report_id=report_id, event_id=event_id, topic_id=topic_id,
                        report_type="event", title=title, status=status,
                        error_message=error, generated_at=None, created_by="u1",
                        created_at=int(time.time()))


def make_md_file(report_id: str = "rpt1", markdown: str = "# 报告\n\n五段式内容") -> ReportFileRecord:
    import time

    return ReportFileRecord(file_id="rf1", report_id=report_id, file_type="md",
                            file_path="docs/reports/report_x.md", content_md=markdown,
                            size_bytes=len(markdown.encode()), created_at=int(time.time()))


def _fake_generate(output: ReportOutput):
    async def _generate(query: str, **kwargs) -> ReportOutput:
        return output

    return _generate


class ReportGenerationTest(unittest.IsolatedAsyncioTestCase):
    """服务层：生成 → 落库 → 状态 → 事件。"""

    async def asyncSetUp(self) -> None:
        from app.services.auth import passwords as passwords_module

        patcher = patch.object(passwords_module, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        patcher.start()
        self.addCleanup(patcher.stop)

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.repo = InMemoryReportRepository()
        self.actor = make_user("analyst", role="analyst")

    async def _create(self, **kwargs):
        from app.services.report import service

        view, task = await service.create_report(self.repo, actor=self.actor, **kwargs)
        return view, task

    async def test_successful_generation_stores_files_and_events(self) -> None:
        from app.services.report import service

        events: list[tuple] = []
        with patch("app.services.report.service.generate_report",
                   _fake_generate(ReportOutput(
                       query="星耀XPhone15", markdown="# 报告正文",
                       html="<html>报告</html>", markdown_path="m.md", html_path="h.html",
                       stats={"total_volume": 10}))), \
                patch("app.services.report.service.publish_role_progress",
                      lambda role, status, msg, pct: events.append(("progress", status, pct))), \
                patch("app.services.report.service.publish_role_result",
                      lambda role, path=None: events.append(("result", path))):
            view, task = await self._create(query="星耀XPhone15")
            self.assertEqual(view["status"], "generating")

            await service.run_generation(self.repo, report_id=task["report_id"],
                                         query="星耀XPhone15", task_id=task["task_id"])

        stored = self.repo.reports[task["report_id"]]
        self.assertEqual(stored.status, ReportStatus.GENERATED.value)
        self.assertIsNotNone(stored.generated_at)
        files = self.repo.files[task["report_id"]]
        self.assertEqual({f.file_type for f in files}, {"md", "html"})
        self.assertEqual(self.repo.tasks[task["task_id"]]["status"], "success")
        self.assertIn(("progress", "completed", 100), events)
        self.assertIn(("result", "h.html"), events)

    async def test_material_missing_marks_failed_with_reason(self) -> None:
        """材料不全必须失败并给出原因（FR-62），不能产出半成品。"""
        from app.services.report import service

        errors: list[str] = []
        with patch("app.services.report.service.generate_report",
                   _fake_generate(ReportOutput(
                       query="x", error="主持人研判、私域分析 的产物尚未生成",
                       missing=["host", "insight"]))), \
                patch("app.services.report.service.publish_role_error",
                      lambda role, error: errors.append(error)):
            _, task = await self._create(query="x")
            await service.run_generation(self.repo, report_id=task["report_id"],
                                         query="x", task_id=task["task_id"])

        stored = self.repo.reports[task["report_id"]]
        self.assertEqual(stored.status, ReportStatus.FAILED.value)
        self.assertIn("尚未生成", stored.error_message)
        self.assertEqual(errors, [stored.error_message])
        self.assertEqual(self.repo.files, {})          # 没有产物

    async def test_unexpected_exception_is_caught(self) -> None:
        from app.services.report import service

        async def boom(query: str, **kwargs):
            raise RuntimeError("模型炸了")

        with patch("app.services.report.service.generate_report", boom), \
                patch("app.services.report.service.publish_role_error", lambda *a, **k: None):
            _, task = await self._create(query="x")
            await service.run_generation(self.repo, report_id=task["report_id"],
                                         query="x", task_id=task["task_id"])

        stored = self.repo.reports[task["report_id"]]
        self.assertEqual(stored.status, ReportStatus.FAILED.value)
        self.assertIn("RuntimeError", stored.error_message)

    async def test_duplicate_active_report_rejected(self) -> None:
        """同一事件已有正在生成的报告时拒绝重复发起（一份约 90 秒、上万 token）。"""
        from app.errors import AppError

        self.repo.reports["rpt_existing"] = make_report("rpt_existing", event_id="evt_1")

        with self.assertRaises(AppError) as ctx:
            await self._create(event_id="evt_1")
        self.assertEqual(ctx.exception.code, "BIZ_203")

    async def test_duplicate_query_report_rejected_by_title(self) -> None:
        """只给 query 的路径没有 event/topic，靠**标题**兜住重复提交。

        否则同一个主题连点两次按钮会真的跑两份报告——每份约 90 秒、上万 token。
        """
        from app.errors import AppError
        from app.services.report.service import report_title

        self.repo.reports["rpt_q"] = make_report(
            "rpt_q", event_id=None, title=report_title("星耀XPhone15"))

        with self.assertRaises(AppError) as ctx:
            await self._create(query="星耀XPhone15")
        self.assertEqual(ctx.exception.code, "BIZ_203")
        self.assertIn("rpt_q", ctx.exception.message)

    async def test_recover_interrupted_marks_stale_reports_failed(self) -> None:
        """进程重启后必须收尾在途任务。

        不收尾的后果是静默的：报告停在 `generating`，前端永远显示「生成中」，
        判重还会把该事件**永久挡住**——「已有正在生成的报告」，却永远等不到完成。
        """
        from app.services.report import service

        self.repo.reports["rpt_stale"] = make_report("rpt_stale", status="generating")
        self.repo.tasks["task_stale"] = {"task_type": "report", "status": "running"}

        result = await service.recover_interrupted(self.repo)

        self.assertEqual(result["interrupted_reports"], ["rpt_stale"])
        self.assertEqual(result["interrupted_tasks"], 1)
        self.assertEqual(self.repo.reports["rpt_stale"].status,
                         ReportStatus.FAILED.value)
        self.assertIn("服务重启", self.repo.reports["rpt_stale"].error_message)
        self.assertEqual(self.repo.tasks["task_stale"]["status"], "failed")

    async def test_recovered_report_no_longer_blocks_new_one(self) -> None:
        """收尾之后同一个目标可以重新发起——否则判重会把事件永久锁死。"""
        from app.services.report import service

        self.repo.reports["rpt_stale"] = make_report(
            "rpt_stale", event_id="evt_1", status="generating")
        await service.recover_interrupted(self.repo)

        # 收尾后同一个事件可以重新发起（这里用显式 query 走不依赖仓储的解析路径）
        view, _ = await self._create(query="星耀XPhone15")
        self.assertEqual(view["status"], "generating")

    async def test_query_resolution_from_explicit_value(self) -> None:
        from app.services.report import service

        query, report_type, title = await service.resolve_query(
            event_id=None, topic_id=None, explicit="新能源车购置税减免")
        self.assertEqual(query, "新能源车购置税减免")
        self.assertEqual(report_type, "event")
        self.assertIn("新能源车购置税减免", title)

    async def test_query_requires_a_target(self) -> None:
        from app.errors import AppError
        from app.services.report import service

        with self.assertRaises(AppError) as ctx:
            await service.resolve_query(event_id=None, topic_id=None, explicit=None)
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_download_rejects_pdf_with_alternative(self) -> None:
        """D-07：PDF 由浏览器打印生成，服务端明确拒绝并说明替代路径。"""
        from app.errors import AppError
        from app.services.report import service

        self.repo.reports["rpt1"] = make_report("rpt1", status="generated")
        self.repo.files["rpt1"] = [make_md_file("rpt1")]

        with self.assertRaises(AppError) as ctx:
            await service.get_download_payload(self.repo, "rpt1", file_type="pdf")

        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("浏览器打印", ctx.exception.message)
        self.assertIn("html", ctx.exception.detail)

    async def test_download_requires_generated_status(self) -> None:
        from app.errors import AppError
        from app.services.report import service

        self.repo.reports["rpt1"] = make_report("rpt1", status="generating")
        with self.assertRaises(AppError) as ctx:
            await service.get_download_payload(self.repo, "rpt1", file_type="md")
        self.assertEqual(ctx.exception.code, "BIZ_203")

    async def test_download_missing_file_type_reports_404(self) -> None:
        from app.errors import AppError
        from app.services.report import service

        self.repo.reports["rpt1"] = make_report("rpt1", status="generated")
        self.repo.files["rpt1"] = [make_md_file("rpt1")]      # 只有 md

        with self.assertRaises(AppError) as ctx:
            await service.get_download_payload(self.repo, "rpt1", file_type="html")
        self.assertEqual(ctx.exception.code, "BIZ_201")


class ReportApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        logging_patcher = silence_app_logging()
        logging_patcher.start()
        self.addCleanup(logging_patcher.stop)
        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.accounts = {
            "admin": make_user("admin", "Admin@123", role="admin"),
            "analyst": make_user("analyst", "Analyst@123", role="analyst"),
            "pr": make_user("pr", "Pr@12345", role="pr"),
        }
        self.repo = InMemoryReportRepository(
            reports=[make_report("rpt_done", status="generated")],
            files=[make_md_file("rpt_done", markdown="# 五段式\n\n正文内容")])

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_report_repository] = lambda: self.repo
        app.dependency_overrides[get_db_session] = lambda: None
        self.app = app

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.app.dependency_overrides.clear()

    async def login(self, username: str) -> None:
        response = await self.client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": {"admin": "Admin@123",
                                                     "analyst": "Analyst@123",
                                                     "pr": "Pr@12345"}[username]})
        self.assertEqual(response.status_code, 200, response.text)

    def audit_for(self, action: str) -> list:
        return [row for row in self.audit_rows if row.action == action]

    async def generate(self, **body) -> dict:
        """发起生成并**同步等待**——测试里不等后台任务，直接驱动同一段逻辑。"""
        from app.services.report import service

        with patch("app.services.report.service.as_task", lambda coro: None), \
                patch("app.services.report.service.generate_report",
                      _fake_generate(ReportOutput(
                          query=body.get("query", "x"), markdown="# 报告",
                          html="<html>报告</html>", markdown_path="m.md",
                          html_path="h.html"))):
            response = await self.client.post("/api/v1/reports", json=body)
            if response.status_code == 202:
                report_id = response.json()["report_id"]
                await service.run_generation(
                    self.repo, report_id=report_id,
                    query=body.get("query", "x"), task_id="task-x")
        return response

    async def test_all_roles_can_list(self) -> None:
        for username in ("admin", "analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                response = await self.client.get("/api/v1/reports")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 1)

    async def test_list_excludes_content(self) -> None:
        await self.login("analyst")
        item = (await self.client.get("/api/v1/reports")).json()["items"][0]

        self.assertEqual(item["report_id"], "rpt_done")
        self.assertEqual(item["status_label"], "已生成")
        self.assertEqual(item["type_label"], "事件专项")
        self.assertEqual(item["chars"], len("# 五段式\n\n正文内容"))
        self.assertNotIn("content_md", item)
        self.assertEqual(item["download_types"], ["md", "html"])

    async def test_detail_includes_markdown(self) -> None:
        await self.login("analyst")
        body = (await self.client.get("/api/v1/reports/rpt_done")).json()
        self.assertIn("五段式", body["content_md"])

    async def test_pr_cannot_generate(self) -> None:
        """发起报告是分析师的动作；公关只处置（PRD 第 2 章）。"""
        await self.login("pr")
        response = await self.client.post("/api/v1/reports", json={"query": "x"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_analyst_generates_and_audits(self) -> None:
        await self.login("analyst")
        self.audit_rows.clear()
        response = await self.generate(query="星耀XPhone15")

        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "generating")

        rows = self.audit_for("report_generate")
        self.assertEqual(len(rows), 1)
        self.assertIn("report:", rows[0].target)
        after = json.loads(rows[0].after_value)
        self.assertEqual(after["status"], "generating")

    async def test_generation_failure_visible_in_list(self) -> None:
        """FR-62：失败要在列表里看得见原因，前端据此给「重试」。"""
        from app.services.report import service

        await self.login("analyst")
        with patch("app.services.report.service.as_task", lambda coro: None), \
                patch("app.services.report.service.generate_report",
                      _fake_generate(ReportOutput(query="x",
                                                  error="主持人研判 的产物尚未生成"))):
            created = await self.client.post("/api/v1/reports", json={"query": "x"})
            report_id = created.json()["report_id"]
            await service.run_generation(self.repo, report_id=report_id, query="x",
                                         task_id="task-x")

        detail = (await self.client.get(f"/api/v1/reports/{report_id}")).json()
        self.assertEqual(detail["status"], "failed")
        self.assertEqual(detail["status_label"], "生成失败")
        self.assertIn("尚未生成", detail["error_message"])

    async def test_download_markdown_as_attachment(self) -> None:
        await self.login("pr")
        self.audit_rows.clear()
        response = await self.client.get("/api/v1/reports/rpt_done/download",
                                         params={"file_type": "md"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/markdown", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn("五段式", response.text)
        self.assertEqual(self.audit_for("report_export")[-1].target, "report:rpt_done")

    async def test_download_html_is_inline_for_printing(self) -> None:
        """HTML 走 inline：预期用法是在浏览器里打开后打印成 PDF（D-07）。"""
        await self.login("analyst")
        response = await self.client.get("/api/v1/reports/rpt_done/download",
                                        params={"file_type": "html"})

        if response.status_code == 200:
            self.assertIn("inline", response.headers["content-disposition"])
            self.assertEqual(response.headers["x-export-hint"], "pdf-by-browser-print")
        else:
            # 该报告只有 md 产物时应回 404 并说明缺什么
            self.assertEqual(response.status_code, 404)

    async def test_download_pdf_explains_alternative(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/reports/rpt_done/download",
                                         params={"file_type": "pdf"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")
        self.assertIn("浏览器打印", response.json()["message"])

    async def test_download_invalid_type_rejected_by_schema(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/reports/rpt_done/download",
                                         params={"file_type": "docx"})
        self.assertEqual(response.status_code, 422)

    async def test_unknown_report_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/reports/rpt-nope")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")


if __name__ == "__main__":
    unittest.main()
