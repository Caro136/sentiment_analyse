"""处置工单接口测试（离线）。

接口层才会暴露的四件事：

1. **关闭权限按目标状态判定**：同一个 `PUT /tickets/{id}`，目标为
   `resolved`/`ignored` 时仅公关可执行（分析师 → 403）；
2. **管理员完全不在处置链路里**（读、写、关都没有）；
3. **轨迹可读**（FR-50 的处置轨迹按时间正序）；
4. **草案三段式**：生成不落库、采纳才写库并进审计。
"""

import json
import unittest
from unittest.mock import patch

from httpx2 import ASGITransport, AsyncClient

from app.main import create_app
from app.routers.tickets import (
    get_content_repository,
    get_event_repository,
    get_ticket_repository,
)
from app.security import get_user_repository
from app.services.auth import passwords
from app.services.ticket.draft import DraftContext, ResponseDraft
from engines.common.db.session import get_db_session
from tests.fakes import (
    InMemoryContentRepository,
    InMemoryEventRepository,
    InMemoryTicketRepository,
    InMemoryUserRepository,
    capture_audit_writes,
    make_analysis,
    make_content,
    make_event,
    make_ticket,
    make_user,
    patch_raw_layer,
)

EVENT_ID = "evt_1"
WEIBO_1 = "social_media:weibo_note:1"

DRAFT = ResponseDraft(
    fact_clarification="已确认涉事门店为加盟店，其他门店不受影响。",
    emotion_guidance="理解公众对食品安全的担忧，我们与大家同样重视。",
    action_plan="已启动全量门店排查，7 日内公布结果并开通退换通道。",
    caution="涉事门店的加盟合同细节需法务确认后再公开。",
)


class TicketApiTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.accounts = {
            "admin": make_user("admin", "Admin@123", role="admin"),
            "analyst": make_user("analyst", "Analyst@123", role="analyst"),
            "pr": make_user("pr", "Pr@12345", role="pr"),
        }

        self.tickets = InMemoryTicketRepository()
        self.events = InMemoryEventRepository(
            events=[make_event(EVENT_ID, title="味鲜居后厨")],
            links={EVENT_ID: [WEIBO_1]})
        self.contents = InMemoryContentRepository(
            records=[make_analysis(WEIBO_1, sentiment="negative", risk_level="S",
                                   topic_id="tp1")])
        self.raw = {WEIBO_1: make_content(WEIBO_1, text="后厨油污堆积", published_at=1792044000)}

        app = create_app()
        app.dependency_overrides[get_user_repository] = lambda: InMemoryUserRepository(
            list(self.accounts.values()))
        app.dependency_overrides[get_ticket_repository] = lambda: self.tickets
        app.dependency_overrides[get_event_repository] = lambda: self.events
        app.dependency_overrides[get_content_repository] = lambda: self.contents
        app.dependency_overrides[get_db_session] = lambda: None
        self.app = app

        self.audit_rows: list = []
        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        raw_layer = patch_raw_layer(hits=[], contents=self.raw)
        raw_layer.__enter__()
        self.addCleanup(raw_layer.__exit__, None, None, None)

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

    async def open_ticket(self, *, as_user: str = "analyst", **overrides) -> dict:
        await self.login(as_user)
        payload = {"event_id": EVENT_ID, "note": "从预警开单"}
        payload.update(overrides)
        response = await self.client.post("/api/v1/tickets", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()


class TicketFlowApiTest(TicketApiTestCase):
    async def test_analyst_creates_ticket_with_trace(self) -> None:
        body = await self.open_ticket()

        self.assertEqual(body["status"], "pending")
        self.assertEqual(body["status_label"], "待研判")
        # 工单初始风险取事件的**生效风险**（由其内容的最高风险现算），
        # 不是读 event.risk_level 列——那一列在本项目里是空的
        self.assertEqual(body["risk_level"], "S")
        self.assertEqual(len(body["logs"]), 1)
        self.assertEqual(body["logs"][0]["action_label"], "创建工单")

    async def test_pr_can_create_ticket(self) -> None:
        body = await self.open_ticket(as_user="pr")
        self.assertEqual(body["status"], "pending")

    async def test_admin_cannot_read_or_create_tickets(self) -> None:
        """管理员不参与处置：读都没有（PRD 第 2 章）。"""
        await self.login("admin")
        listed = await self.client.get("/api/v1/tickets")
        created = await self.client.post("/api/v1/tickets", json={"event_id": EVENT_ID})

        for response in (listed, created):
            with self.subTest(path=response.request.url.path):
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_duplicate_open_ticket_rejected(self) -> None:
        await self.open_ticket()
        duplicate = await self.client.post("/api/v1/tickets", json={"event_id": EVENT_ID})

        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(duplicate.json()["code"], "BIZ_203")

    async def test_flow_records_trace(self) -> None:
        created = await self.open_ticket()
        tid = created["ticket_id"]
        self.audit_rows.clear()

        tracked = await self.client.put(f"/api/v1/tickets/{tid}",
                                        json={"status": "tracking", "note": "已联系门店"})
        self.assertEqual(tracked.status_code, 200)
        body = tracked.json()
        self.assertEqual(body["status_label"], "跟踪中")
        self.assertEqual([log["action"] for log in body["logs"]], ["create", "claim"])

        row = self.audit_for("ticket_flow")[-1]
        self.assertEqual(row.target, f"ticket:{tid}")
        self.assertEqual(row.user_id, self.accounts["analyst"].user_id)
        self.assertEqual(json.loads(row.before_value)["status"], "pending")
        self.assertEqual(json.loads(row.after_value)["status"], "tracking")

    async def test_comment_without_status(self) -> None:
        created = await self.open_ticket()
        response = await self.client.put(f"/api/v1/tickets/{created['ticket_id']}",
                                        json={"note": "补充：已取证实物"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["logs"][-1]["action"], "comment")

    async def test_detail_lists_trace_in_time_order(self) -> None:
        created = await self.open_ticket()
        tid = created["ticket_id"]
        await self.client.put(f"/api/v1/tickets/{tid}",
                              json={"status": "verified", "note": "已核实属实"})

        detail = (await self.client.get(f"/api/v1/tickets/{tid}")).json()
        times = [log["operated_at"] for log in detail["logs"]]
        self.assertEqual(times, sorted(times))
        self.assertEqual(detail["logs"][-1]["from_status_label"], "待研判")
        self.assertEqual(detail["logs"][-1]["to_status_label"], "已核实")
        self.assertEqual(detail["logs"][-1]["note"], "已核实属实")

    async def test_allowed_transitions_exposed(self) -> None:
        """前端按 allowed_transitions 渲染可选项，避免前端手写一份状态机。"""
        created = await self.open_ticket()
        self.assertIn("tracking", created["allowed_transitions"])
        self.assertIn("resolved", created["allowed_transitions"])

        await self.client.put(f"/api/v1/tickets/{created['ticket_id']}",
                              json={"status": "verified"})
        detail = (await self.client.get(
            f"/api/v1/tickets/{created['ticket_id']}")).json()
        self.assertNotIn("tracking", detail["allowed_transitions"])   # 不能回退

    async def test_illegal_transition_returns_409(self) -> None:
        created = await self.open_ticket()
        tid = created["ticket_id"]
        await self.client.put(f"/api/v1/tickets/{tid}", json={"status": "verified"})

        response = await self.client.put(f"/api/v1/tickets/{tid}", json={"status": "tracking"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "BIZ_203")
        self.assertIn("已核实", response.json()["message"])

    async def test_unknown_ticket_returns_404(self) -> None:
        await self.login("analyst")
        response = await self.client.get("/api/v1/tickets/tk-nope")
        self.assertEqual(response.status_code, 404)


class TicketClosePermissionTest(TicketApiTestCase):
    """关闭动作按目标状态判权：同一个接口、不同的目标状态、不同的权限。"""

    async def test_analyst_cannot_close(self) -> None:
        created = await self.open_ticket()
        tid = created["ticket_id"]

        for target in ("resolved", "ignored"):
            with self.subTest(target=target):
                response = await self.client.put(f"/api/v1/tickets/{tid}",
                                                json={"status": target})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["code"], "AUTH_003")

    async def test_pr_can_close_and_records_closed_at(self) -> None:
        created = await self.open_ticket()
        tid = created["ticket_id"]
        await self.client.put(f"/api/v1/tickets/{tid}", json={"status": "tracking"})

        await self.login("pr")
        self.audit_rows.clear()
        response = await self.client.put(f"/api/v1/tickets/{tid}",
                                        json={"status": "resolved", "note": "已发布澄清声明"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status_label"], "已处理")
        self.assertTrue(body["is_closed"])
        self.assertIsNotNone(body["closed_at"])
        self.assertEqual(body["allowed_transitions"], [])       # 终态无可选项
        self.assertEqual(self.audit_for("ticket_flow")[-1].user_id,
                         self.accounts["pr"].user_id)

    async def test_closed_ticket_rejects_further_flow(self) -> None:
        created = await self.open_ticket()
        tid = created["ticket_id"]
        await self.login("pr")
        await self.client.put(f"/api/v1/tickets/{tid}", json={"status": "resolved"})

        response = await self.client.put(f"/api/v1/tickets/{tid}", json={"status": "tracking"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("新建工单", response.json()["message"])

    async def test_new_ticket_allowed_after_close(self) -> None:
        created = await self.open_ticket()
        await self.login("pr")
        await self.client.put(f"/api/v1/tickets/{created['ticket_id']}",
                              json={"status": "ignored"})

        await self.login("analyst")
        again = await self.client.post("/api/v1/tickets", json={"event_id": EVENT_ID})
        self.assertEqual(again.status_code, 201)


class ResponseDraftApiTest(TicketApiTestCase):
    async def test_generate_returns_three_sections(self) -> None:
        await self.login("pr")
        with patch("app.services.ticket.draft.generate_draft",
                   _fake_generate(DRAFT)):
            response = await self.client.post(
                f"/api/v1/events/{EVENT_ID}/response-draft",
                json={"cluster_labels": ["要求退款并质疑散热"]})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        # 三段的内容在字段里，小标题在 rendered 里（前者给编辑器，后者给人读）
        self.assertIn("加盟店", body["draft"]["fact_clarification"])
        self.assertIn("理解公众", body["draft"]["emotion_guidance"])
        self.assertIn("全量门店排查", body["draft"]["action_plan"])
        self.assertIn("法务确认", body["draft"]["caution"])
        self.assertIn("【事实澄清】", body["rendered"])
        self.assertIn("【发布前请确认】", body["rendered"])
        self.assertIn("要求退款并质疑散热", body["evidence"]["cluster_labels"])

    async def test_generation_does_not_persist(self) -> None:
        """生成是探索动作：库里不该出现半成品。"""
        await self.login("pr")
        with patch("app.services.ticket.draft.generate_draft", _fake_generate(DRAFT)):
            await self.client.post(f"/api/v1/events/{EVENT_ID}/response-draft", json={})

        self.assertEqual(self.tickets.tickets, {})

    async def test_admin_cannot_generate_draft(self) -> None:
        await self.login("admin")
        response = await self.client.post(f"/api/v1/events/{EVENT_ID}/response-draft",
                                         json={})
        self.assertEqual(response.status_code, 403)

    async def test_llm_failure_reports_ana_301(self) -> None:
        from app.errors import AppError

        await self.login("pr")

        async def boom(context, *, client=None):
            raise AppError("ANA_301", "回应草案生成失败：模型未配置")

        with patch("app.services.ticket.draft.generate_draft", boom):
            response = await self.client.post(f"/api/v1/events/{EVENT_ID}/response-draft",
                                              json={})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["code"], "ANA_301")

    async def test_unknown_event_returns_404(self) -> None:
        await self.login("pr")
        response = await self.client.post("/api/v1/events/evt-none/response-draft", json={})
        self.assertEqual(response.status_code, 404)

    async def test_adopt_writes_draft_and_audit(self) -> None:
        created = await self.open_ticket(as_user="pr")
        tid = created["ticket_id"]
        self.audit_rows.clear()

        response = await self.client.post(
            f"/api/v1/tickets/{tid}/draft",
            json={"content": "【事实澄清】已确认…\n【行动方案】7 日内公布排查结果",
                  "note": "已与法务确认"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["has_response_draft"])
        self.assertIn("7 日内公布排查结果", body["response_draft"])
        self.assertEqual(body["logs"][-1]["action_label"], "采纳回应草案")

        rows = self.audit_for("draft_adopt")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].target, f"ticket:{tid}")
        self.assertEqual(rows[0].user_id, self.accounts["pr"].user_id)

    async def test_adopt_without_content_returns_422(self) -> None:
        created = await self.open_ticket(as_user="pr")
        response = await self.client.post(f"/api/v1/tickets/{created['ticket_id']}/draft",
                                         json={})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "BIZ_400")


def _fake_generate(draft: ResponseDraft):
    """把模型调用换成固定草案——生成逻辑本身由 `build_context` 的测试覆盖。"""
    async def _generate(context: DraftContext, *, client=None) -> ResponseDraft:
        return draft

    return _generate


class DraftContextTest(TicketApiTestCase):
    async def test_context_collects_evidence_from_labels(self) -> None:
        from app.services.ticket import draft as draft_module

        with patch_raw_layer(hits=[], contents=self.raw):
            context = await draft_module.build_context(
                self.events, self.contents, None, EVENT_ID,
                cluster_labels=["要求退款并质疑散热"])

        self.assertEqual(context.title, "味鲜居后厨")
        self.assertEqual(context.risk_level, "S")
        self.assertEqual(context.negative_ratio, 1.0)
        self.assertEqual(context.sentiment_distribution, {"negative": 1})
        self.assertEqual(context.cluster_labels, ["要求退款并质疑散热"])
        self.assertTrue(context.high_risk_quotes)
        self.assertIn("后厨油污堆积", context.high_risk_quotes[0])

    async def test_context_without_evidence_blocks_generation(self) -> None:
        """事件没有关联内容时拒绝生成——不产出「看起来很正式」的空话草稿。

        素材门槛与报告引擎同一条纪律（FR-62）：材料不全就说缺什么。
        """
        from app.errors import AppError
        from app.services.ticket import draft as draft_module

        empty_event = make_event("evt_empty", title="尚无数据的事件")
        empty_events = InMemoryEventRepository(events=[empty_event], links={})

        with patch_raw_layer(hits=[], contents={}):
            context = await draft_module.build_context(
                empty_events, InMemoryContentRepository(), None, "evt_empty")

        self.assertEqual(context.total_volume, 0)
        self.assertEqual(context.high_risk_quotes, [])
        with self.assertRaises(AppError) as ctx:
            draft_module.ensure_generatable(context)
        self.assertEqual(ctx.exception.code, "BIZ_410")

    async def test_context_with_evidence_is_generatable(self) -> None:
        from app.services.ticket import draft as draft_module

        with patch_raw_layer(hits=[], contents=self.raw):
            context = await draft_module.build_context(
                self.events, self.contents, None, EVENT_ID)

        draft_module.ensure_generatable(context)      # 不抛异常即通过

    async def test_context_for_unknown_event_raises_404(self) -> None:
        from app.errors import AppError
        from app.services.ticket import draft as draft_module

        with self.assertRaises(AppError) as ctx:
            await draft_module.build_context(self.events, self.contents, None, "evt-nope")
        self.assertEqual(ctx.exception.code, "BIZ_201")


if __name__ == "__main__":
    unittest.main()
