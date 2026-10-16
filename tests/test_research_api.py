"""研判接口测试（API-22/23，FR-64~66）。

接口层才会暴露的四件事：

1. **异步语义**：POST 立即 202 返回 task_id，任务在后台跑；
2. **单任务约束**：已有任务在跑时 409（硬约束：主持人会话是进程内共享的）；
3. **审计**：发起动作留痕（FR-04）——它要花模型额度、要落产物；
4. **权限**：发起需 `research:run`（分析师 / 管理员），查询三方皆可。

**编排器是替身**：真跑一次研判要调模型、要落盘（数分钟），
测试只需验证「接缝」——参数传对、状态码对、异常翻译对。
"""

import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.security import get_user_repository
from app.services.auth import passwords
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryUserRepository,
    capture_audit_writes,
    make_user,
    silence_app_logging,
)


class FakeOrchestrator:
    """编排器替身：只记下被调用时的参数，并按需返回任务。"""

    def __init__(self, *, running: bool = False) -> None:
        self.running = running
        self.started: list[dict] = []
        self.task = _FakeTask("res_test1", "星耀XPhone15")

    @property
    def current(self):
        return self.task if self.running else None

    def get_task(self, task_id):
        return self.task if task_id == self.task.task_id else None

    async def start(self, query, *, roles=None):
        if self.running:
            raise RuntimeError("已有研判任务在执行（res_test1），请先停止或等待完成")
        self.started.append({"query": query, "roles": roles})
        self.task = _FakeTask("res_test1", query)
        self.running = True
        return self.task


class _FakeTask:
    def __init__(self, task_id: str, query: str) -> None:
        self.task_id = task_id
        self.query = query
        self.status = "running"

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "query": self.query, "status": self.status,
                "duration": 1.5,
                "results": {"insight": {"display_name": "私域研究员", "ok": True}}}


class FakeMonitoringRepository:
    def __init__(self, topic=("tp1", "味鲜居后厨卫生")) -> None:
        self._topic = topic

    def __call__(self, session) -> "FakeMonitoringRepository":
        return self

    async def get_topic(self, topic_id):
        if self._topic is None:
            return None
        return type("Topic", (), {"topic_id": self._topic[0], "name": self._topic[1]})()


class ResearchApiTest(unittest.IsolatedAsyncioTestCase):
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
        self.orchestrator = FakeOrchestrator()

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_db_session] = lambda: None
        self.app = app

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

        # 编排器与主题仓储都换成替身：测试不碰模型、不碰库
        self.patchers = [
            patch("app.services.research.service.get_orchestrator",
                  lambda: self.orchestrator),
            patch("app.services.research.service.SqlMonitoringRepository",
                  FakeMonitoringRepository()),
            patch("app.services.research.service.session_scope", _fake_session_scope),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

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

    async def test_start_returns_202_with_task_id(self) -> None:
        await self.login("analyst")

        response = await self.client.post("/api/v1/research", json={"query": "星耀XPhone15"})

        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertEqual(body["task_id"], "res_test1")
        self.assertTrue(body["started"])
        self.assertEqual(body["query"], "星耀XPhone15")
        # 默认两个分析角色；主持人由编排层按「先启动」的顺序管理，不由调用方指定
        self.assertEqual(self.orchestrator.started[0]["roles"], ["insight", "media"])

    async def test_start_is_audited(self) -> None:
        await self.login("analyst")

        await self.client.post("/api/v1/research", json={"query": "星耀XPhone15"})

        rows = [row for row in self.audit_rows if row.action == "research_run"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].user_id, self.accounts["analyst"].user_id)
        self.assertIn("星耀XPhone15", rows[0].target or "")

    async def test_topic_id_resolves_query_from_topic_name(self) -> None:
        await self.login("analyst")

        response = await self.client.post("/api/v1/research", json={"topic_id": "tp1"})

        self.assertEqual(response.status_code, 202, response.text)
        # 研判主题词必须是产物文件名键：给主题 ID 时要解析成主题**名称**
        self.assertEqual(self.orchestrator.started[0]["query"], "味鲜居后厨卫生")

    async def test_unknown_topic_returns_404(self) -> None:
        await self.login("analyst")
        with patch("app.services.research.service.SqlMonitoringRepository",
                   FakeMonitoringRepository(topic=None)):
            response = await self.client.post("/api/v1/research", json={"topic_id": "tp_x"})

        self.assertEqual(response.status_code, 404, response.text)

    async def test_missing_target_is_rejected(self) -> None:
        await self.login("analyst")

        response = await self.client.post("/api/v1/research", json={})

        self.assertIn(response.status_code, (400, 422), response.text)

    async def test_unknown_role_is_rejected(self) -> None:
        await self.login("analyst")

        response = await self.client.post(
            "/api/v1/research", json={"query": "x", "roles": ["insight", "wizard"]})

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.orchestrator.started, [])

    async def test_concurrent_task_returns_409(self) -> None:
        await self.login("analyst")
        self.orchestrator.running = True

        response = await self.client.post("/api/v1/research", json={"query": "星耀XPhone15"})

        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("已有研判任务在执行", response.json()["message"])

    async def test_pr_cannot_start(self) -> None:
        await self.login("pr")

        response = await self.client.post("/api/v1/research", json={"query": "x"})

        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(self.orchestrator.started, [])

    async def test_all_roles_can_read_task(self) -> None:
        for username in ("admin", "analyst", "pr"):
            with self.subTest(user=username):
                await self.login(username)
                response = await self.client.get("/api/v1/research/res_test1")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "running")
                self.assertIn("insight", response.json()["results"])

    async def test_unknown_task_returns_404(self) -> None:
        await self.login("analyst")

        response = await self.client.get("/api/v1/research/res_nope")

        self.assertEqual(response.status_code, 404, response.text)

    async def test_requires_login(self) -> None:
        response = await self.client.post("/api/v1/research", json={"query": "x"})
        self.assertEqual(response.status_code, 401)


def _fake_session_scope():
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def scope():
        yield object()

    return scope()


if __name__ == "__main__":
    unittest.main()
