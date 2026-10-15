"""系统运维接口测试（FR-72~FR-75）。

权限面比功能面重要：任务看板与日志检索只给管理员（PRD 第 2 章），
重置还要确认口令，且**非管理员试重置必须被权限挡住**（不能靠口令兜底）。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.system import get_reset_executor, get_task_repository
from app.security import get_user_repository
from app.services.auth import passwords
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryUserRepository,
    capture_audit_writes,
    make_user,
    silence_app_logging,
)


class InMemoryTaskRepository:
    """任务仓储替身（列表、汇总、埋点）。"""

    def __init__(self, tasks: list | None = None) -> None:
        from app.services.system.tasks import TaskRecord

        self.tasks: list[TaskRecord] = list(tasks or [])
        self.calls: list[dict] = []

    async def start(self, *, task_id, task_type, input_summary):
        self.calls.append({"task_id": task_id, "task_type": task_type})

    async def finish(self, task_id, *, status, error=""):
        self.calls.append({"task_id": task_id, "status": status})

    async def list_tasks(self, *, page, page_size, task_type=None, status=None,
                         start_time=None, end_time=None):
        rows = list(self.tasks)
        if task_type:
            rows = [t for t in rows if t.task_type == task_type]
        if status:
            rows = [t for t in rows if t.status == status]
        rows.sort(key=lambda t: t.started_at, reverse=True)
        start = (page - 1) * page_size
        return len(rows), rows[start:start + page_size]

    async def counts_by_status(self):
        counts: dict[str, int] = {}
        for task in self.tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return counts

    async def summary_by_type(self):
        summary: dict[str, dict] = {}
        for task in self.tasks:
            item = summary.setdefault(task.task_type, {
                "task_type": task.task_type, "total": 0, "success": 0,
                "failed": 0, "running": 0, "avg_duration_ms": None})
            item["total"] += 1
            item[task.status] += 1
        return sorted(summary.values(), key=lambda item: item["task_type"])


class FakeResetExecutor:
    """重置执行器替身：预置若干行数，记录删了哪些表。"""

    def __init__(self, row_counts: dict[str, int] | None = None) -> None:
        self.row_counts = row_counts or {"content_analysis": 2825, "event": 1,
                                         "cluster_member": 1193}
        self.deleted: list[str] = []

    async def count(self, table: str) -> int:
        return self.row_counts.get(table, 0)

    async def delete(self, table: str) -> int:
        self.deleted.append(table)
        return self.row_counts.get(table, 0)


def make_task(task_type: str = "report", status: str = "success",
              duration_ms: int | None = 53000, task_id: str = "task_x") -> "object":
    import time

    from app.services.system.tasks import TaskRecord

    started = int(time.time()) - 60
    return TaskRecord(task_id=task_id, task_type=task_type, status=status,
                      started_at=started,
                      finished_at=(started + 53 if status != "running" else None),
                      duration_ms=(duration_ms if status != "running" else None),
                      input_summary="query=新能源车购置税减免", error_message=None)


class SystemApiTestCase(unittest.IsolatedAsyncioTestCase):
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
        self.tasks = InMemoryTaskRepository(tasks=[
            make_task("report", "success", 53000, "task_report_1"),
            make_task("dashboard", "success", 300, "task_dashboard_1"),
            make_task("alert", "running", None, "task_alert_1"),
        ])

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_task_repository] = lambda: self.tasks
        # 重置执行器换成替身：它必须能在离线测试里被覆盖（最危险的入口）
        app.dependency_overrides[get_reset_executor] = lambda: FakeResetExecutor()
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


class TaskBoardApiTest(SystemApiTestCase):
    async def test_admin_can_read_task_board(self) -> None:
        await self.login("admin")
        body = (await self.client.get("/api/v1/system/tasks")).json()

        self.assertEqual(body["total"], 3)
        first = body["items"][0]
        self.assertIn("task_id", first)
        self.assertIn("duration_ms", first)

    async def test_business_roles_cannot_read(self) -> None:
        for username in ("analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                for path in ("/api/v1/system/tasks", "/api/v1/system/tasks/summary",
                             "/api/v1/system/logs", "/api/v1/system/logs/modules"):
                    response = await self.client.get(path)
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_running_task_reports_elapsed_seconds(self) -> None:
        await self.login("admin")
        items = (await self.client.get("/api/v1/system/tasks",
                                       params={"status": "running"})).json()["items"]
        self.assertEqual(items[0]["task_id"], "task_alert_1")
        self.assertIsNone(items[0]["duration_ms"])
        self.assertGreaterEqual(items[0]["running_seconds"], 0)

    async def test_filter_by_type(self) -> None:
        await self.login("admin")
        body = (await self.client.get("/api/v1/system/tasks",
                                      params={"task_type": "report"})).json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["task_type"], "report")

    async def test_summary(self) -> None:
        await self.login("admin")
        body = (await self.client.get("/api/v1/system/tasks/summary")).json()

        by_type = {item["task_type"]: item for item in body["items"]}
        self.assertEqual(by_type["report"]["success"], 1)
        self.assertEqual(by_type["alert"]["running"], 1)
        self.assertEqual(body["counts_by_status"], {"success": 2, "running": 1})


class LogSearchApiTest(SystemApiTestCase):
    async def test_admin_can_list_modules(self) -> None:
        await self.login("admin")
        body = (await self.client.get("/api/v1/system/logs/modules")).json()
        modules = {item["module"] for item in body}
        self.assertIn("app", modules)
        self.assertIn("report", modules)

    async def test_search_returns_lines(self) -> None:
        await self.login("admin")
        body = (await self.client.get("/api/v1/system/logs",
                                      params={"module": "app", "limit": 5})).json()
        self.assertEqual(body["module"], "app")
        self.assertIn("lines", body)
        self.assertIn("truncated", body)

    async def test_missing_module_returns_empty_not_error(self) -> None:
        """日志文件不存在不是错误（还没跑过该角色），返回空结果即可。"""
        await self.login("admin")
        response = await self.client.get("/api/v1/system/logs",
                                         params={"module": "不存在的模块"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["lines"], [])

    async def test_limit_cap_enforced(self) -> None:
        await self.login("admin")
        response = await self.client.get("/api/v1/system/logs", params={"limit": 9999})
        self.assertEqual(response.status_code, 422)


class ResetApiTest(SystemApiTestCase):
    async def test_non_admin_cannot_reset_even_with_token(self) -> None:
        """权限先于口令：业务角色即使知道口令也进不来。"""
        await self.login("analyst")
        response = await self.client.post("/api/v1/system/reset",
                                          json={"scope": "all", "confirm": "RESET_ALL"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_wrong_token_rejected(self) -> None:
        await self.login("admin")
        response = await self.client.post("/api/v1/system/reset",
                                          json={"scope": "analysis", "confirm": "随便写"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("RESET_ANALYSIS", response.json()["message"])

    async def test_raw_scope_rejected(self) -> None:
        await self.login("admin")
        response = await self.client.post("/api/v1/system/reset",
                                          json={"scope": "raw", "confirm": "RESET_RAW"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("原始区", response.json()["message"])

    async def test_dry_run_reports_impact_without_deleting(self) -> None:
        await self.login("admin")
        self.audit_rows.clear()
        response = await self.client.post("/api/v1/system/reset", json={
            "scope": "analysis", "confirm": "RESET_ANALYSIS", "dry_run": True})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["scope"], "analysis")
        self.assertEqual(body["deleted"], {})
        self.assertIn("content_analysis", body["tables"])
        self.assertIn("sys_user", body["protected_tables"])
        # 只看不删也要留痕：这是「有人动过重置入口」的事实
        rows = self.audit_for("data_reset")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].target, "reset:analysis")
        self.assertTrue(json.loads(rows[0].after_value)["dry_run"])

    async def test_apply_deletes_only_whitelisted_tables(self) -> None:
        await self.login("admin")
        executor = FakeResetExecutor()
        self.app.dependency_overrides[get_reset_executor] = lambda: executor

        response = await self.client.post("/api/v1/system/reset", json={
            "scope": "analysis", "confirm": "RESET_ANALYSIS"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(set(executor.deleted),
                         set(__import__("app.services.system.reset",
                                        fromlist=["RESET_SCOPES"]).RESET_SCOPES["analysis"]))
        self.assertNotIn("sys_user", executor.deleted)
        self.assertNotIn("audit_log", executor.deleted)
        body = response.json()
        self.assertEqual(body["deleted"]["content_analysis"], 2825)
        self.assertTrue(body["protected_tables"])

    async def test_all_scope_clears_each_group(self) -> None:
        await self.login("admin")
        executor = FakeResetExecutor()
        self.app.dependency_overrides[get_reset_executor] = lambda: executor

        response = await self.client.post("/api/v1/system/reset", json={
            "scope": "all", "confirm": "RESET_ALL"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("report_file", executor.deleted)
        self.assertIn("alert_record", executor.deleted)
        self.assertIn("content_analysis", executor.deleted)
        self.assertNotIn("topic", executor.deleted)      # 监测配置属于使用者


if __name__ == "__main__":
    unittest.main()
