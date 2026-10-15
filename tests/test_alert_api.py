"""预警接口测试（离线）。

接口层才会暴露的三件事：

1. **权限三分**：查看（三方）/ 认领关闭（分析师 + 公关）/ 规则维护与补扫（仅管理员）；
2. **状态机拒绝非法迁移**（409 而不是静默改状态）；
3. **留痕**：认领与规则阈值变更都要进审计，且带前后值——FR-52 的验收就落在
   「把阈值由 50% 改成 30% 能查到变更前后值」上。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.system import get_task_repository
from app.security import get_user_repository
from app.services.auth import passwords
from engines.alerting.repository import RuleRecord
from engines.alerting.service import ScanResult
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryAlertStore,
    InMemoryUserRepository,
    capture_audit_writes,
    make_alert,
    make_user,
)

RULE_ID = "rule_surge"


class _FakeTaskRepository:
    """任务埋点替身：只记录调用，便于断言「扫描确实写了任务」。"""

    def __init__(self, calls: list) -> None:
        self._calls = calls

    async def start(self, *, task_id, task_type, input_summary):
        self._calls.append({"task_id": task_id, "task_type": task_type,
                            "phase": "start"})

    async def finish(self, task_id, *, status, error="", duration_ms=None):
        self._calls.append({"task_id": task_id, "status": status, "phase": "finish",
                            "duration_ms": duration_ms})


class AlertApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.accounts = {
            "admin": make_user("admin", "Admin@123", role="admin"),
            "analyst": make_user("analyst", "Analyst@123", role="analyst"),
            "pr": make_user("pr", "Pr@12345", role="pr"),
        }

        self.store = InMemoryAlertStore(
            rules=[RuleRecord(rule_id=RULE_ID, rule_type="negative_surge", level="A",
                              threshold=0.5, sensitive_words=(), enabled=True,
                              updated_at=1792040000)],
            alerts=[make_alert("alert_1", status="unread", level="A"),
                    make_alert("alert_2", status="closed", level="S")],
            scan_result=ScanResult(scanned_topics=3, window_contents=2825, hits=2,
                                   created=["alert_new"], merged=["alert_1"],
                                   notified=1, degraded=0, skipped=0))
        self.patcher = self.store.patch()
        self.patcher.__enter__()
        self.addCleanup(self.patcher.__exit__, None, None, None)

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        # 补扫会写一条 task_run（FR-72 的埋点），测试里换成收集用的替身
        self.task_calls: list[dict] = []
        app.dependency_overrides[get_task_repository] = lambda: _FakeTaskRepository(
            self.task_calls)
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


class AlertListApiTest(AlertApiTestCase):
    async def test_all_roles_can_read(self) -> None:
        for username in ("admin", "analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                response = await self.client.get("/api/v1/alerts")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["total"], 2)

    async def test_view_fields(self) -> None:
        await self.login("analyst")
        items = (await self.client.get("/api/v1/alerts")).json()["items"]
        first = next(i for i in items if i["alert_id"] == "alert_1")

        self.assertEqual(first["rule_label"], "负向声量激增")
        self.assertEqual(first["status_label"], "未读")
        self.assertIn("激增", first["reason"])
        self.assertEqual(first["topic_id"], "tp1")
        self.assertIsNone(first["response_seconds"])        # 未认领 → null，不是 0

    async def test_filter_by_status(self) -> None:
        await self.login("analyst")
        closed = await self.client.get("/api/v1/alerts", params={"status": "closed"})
        self.assertEqual(closed.json()["total"], 1)
        self.assertEqual(closed.json()["items"][0]["alert_id"], "alert_2")

    async def test_invalid_status_filter_returns_422(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/alerts", params={"status": "pending"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")

    async def test_responded_alert_reports_duration(self) -> None:
        self.store.alerts[0] = make_alert("alert_1", status="claimed",
                                          triggered_at=1000, responded_at=1180)
        await self.login("analyst")
        item = (await self.client.get("/api/v1/alerts")).json()["items"][0]
        self.assertEqual(item["response_seconds"], 180)


class AlertLifecycleApiTest(AlertApiTestCase):
    async def test_analyst_can_claim(self) -> None:
        self.audit_rows.clear()
        await self.login("analyst")
        response = await self.client.put("/api/v1/alerts/alert_1",
                                        json={"status": "claimed", "note": "我来跟进"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "claimed")
        self.assertIsNotNone(body["responded_at"])
        self.assertEqual(body["response_seconds"] is not None, True)

        row = self.audit_for("alert_ack")[-1]
        self.assertEqual(row.target, "alert:alert_1")
        self.assertEqual(row.user_id, self.accounts["analyst"].user_id)
        before = json.loads(row.before_value)
        after = json.loads(row.after_value)
        self.assertEqual(before["status"], "unread")
        self.assertEqual(after["status"], "claimed")
        self.assertEqual(after["note"], "我来跟进")

    async def test_pr_can_close(self) -> None:
        await self.login("pr")
        response = await self.client.put("/api/v1/alerts/alert_1", json={"status": "closed"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "closed")

    async def test_admin_cannot_ack(self) -> None:
        """管理员只运维不处置：不参与告警认领（PRD 第 2 章）。"""
        await self.login("admin")
        response = await self.client.put("/api/v1/alerts/alert_1",
                                        json={"status": "claimed"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_illegal_transition_rejected(self) -> None:
        """已关闭的告警不能被改回未读——否则告警列表的可信度就没了。"""
        await self.login("analyst")
        response = await self.client.put("/api/v1/alerts/alert_2",
                                        json={"status": "unread"})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "BIZ_203")
        self.assertIn("已关闭", response.json()["message"])

    async def test_same_status_is_noop_marked_in_audit(self) -> None:
        """重复提交同一状态：不写库（responded_at 不被刷新），审计里标为未变更。"""
        await self.login("analyst")
        self.audit_rows.clear()
        response = await self.client.put("/api/v1/alerts/alert_1",
                                        json={"status": "unread"})

        self.assertEqual(response.status_code, 200)
        rows = self.audit_for("alert_ack")
        self.assertEqual(len(rows), 1)
        self.assertFalse(json.loads(rows[0].after_value)["changed"])

    async def test_unknown_alert_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.put("/api/v1/alerts/alert-nope",
                                        json={"status": "claimed"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "BIZ_201")


class AlertRuleApiTest(AlertApiTestCase):
    async def test_non_admin_cannot_manage_rules(self) -> None:
        for username in ("analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                listed = await self.client.get("/api/v1/alerts/rules")
                created = await self.client.post("/api/v1/alerts/rules", json={
                    "rule_type": "sensitive_hit", "level": "S", "sensitive_words": ["退款"]})
                for response in (listed, created):
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_admin_lists_rules(self) -> None:
        await self.login("admin")
        items = (await self.client.get("/api/v1/alerts/rules")).json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type_label"], "负向声量激增")
        self.assertEqual(items[0]["threshold"], 0.5)

    async def test_threshold_change_is_audited_with_before_after(self) -> None:
        """FR-52 的验收口径：阈值由 50% 改为 30% 后能查到变更前后值。"""
        await self.login("admin")
        self.audit_rows.clear()

        response = await self.client.put(f"/api/v1/alerts/rules/{RULE_ID}",
                                         json={"threshold": 0.3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["threshold"], 0.3)

        row = self.audit_for("config_update")[-1]
        self.assertEqual(row.target, f"alert_rule:{RULE_ID}")
        self.assertEqual(json.loads(row.before_value)["threshold"], 0.5)
        self.assertEqual(json.loads(row.after_value)["threshold"], 0.3)

    async def test_create_sensitive_rule_requires_words(self) -> None:
        await self.login("admin")
        response = await self.client.post("/api/v1/alerts/rules", json={
            "rule_type": "sensitive_hit", "level": "S", "sensitive_words": []})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")
        self.assertIn("敏感词库", response.json()["message"])

    async def test_create_rule_fills_default_threshold(self) -> None:
        await self.login("admin")
        response = await self.client.post("/api/v1/alerts/rules", json={
            "rule_type": "kol_voice", "level": "B"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["threshold"], 3)      # KOL 规则默认 3 条

    async def test_cannot_clear_sensitive_words(self) -> None:
        await self.login("admin")
        await self.client.post("/api/v1/alerts/rules", json={
            "rule_type": "sensitive_hit", "level": "S", "sensitive_words": ["退款"]})
        rule_id = self.store.rules[-1].rule_id

        response = await self.client.put(f"/api/v1/alerts/rules/{rule_id}",
                                         json={"sensitive_words": []})
        self.assertEqual(response.status_code, 422)
        self.assertIn("不能把词库清空", response.json()["message"])

    async def test_unknown_rule_returns_404(self) -> None:
        await self.login("admin")
        response = await self.client.put("/api/v1/alerts/rules/rule-nope",
                                         json={"enabled": False})
        self.assertEqual(response.status_code, 404)


class AlertScanApiTest(AlertApiTestCase):
    async def test_admin_can_scan(self) -> None:
        await self.login("admin")
        response = await self.client.post("/api/v1/alerts/scan",
                                          params={"window_minutes": 120, "notify": False})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["window_contents"], 2825)
        self.assertEqual(body["created"], ["alert_new"])
        self.assertEqual(body["summary"], self.store.scan_result.render())
        self.assertEqual(self.store.scan_calls[-1]["window_minutes"], 120)
        self.assertFalse(self.store.scan_calls[-1]["notify"])
        # 扫描写了一条 alert 类型的任务（FR-72 的运维看板要看得到）
        self.assertEqual([call["phase"] for call in self.task_calls],
                         ["start", "finish"])
        self.assertTrue(self.task_calls[0]["task_id"].startswith("task_alert_"))

    async def test_analyst_cannot_scan(self) -> None:
        await self.login("analyst")
        response = await self.client.post("/api/v1/alerts/scan")
        self.assertEqual(response.status_code, 403)

    async def test_invalid_window_rejected(self) -> None:
        await self.login("admin")
        response = await self.client.post("/api/v1/alerts/scan",
                                          params={"window_minutes": 0})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
