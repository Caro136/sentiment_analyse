"""处置工单用例测试（FR-49~FR-51）。

三处容易做错的地方：

1. **状态机**：只能向前、终态不流转——写松了会让「已处理的工单被改回待研判」；
2. **轨迹**：状态与轨迹必须同写，只改状态不记轨迹等于把处置过程丢了（FR-50）；
3. **草案**：生成不落库、采纳才写库，且采纳要进审计（谁决定对外怎么说）。
"""

import unittest

from app.errors import AppError
from app.services.ticket import lifecycle, service
from engines.contracts.enums import TicketStatus
from tests.fakes import (InMemoryContentRepository, InMemoryTicketRepository,
                         make_ticket, make_user)

EVENT_ID = "evt_1"


class LifecycleTest(unittest.TestCase):
    def test_happy_path_is_allowed(self) -> None:
        path = ["pending", "tracking", "verified", "debunked", "resolved"]
        for current, target in zip(path, path[1:]):
            with self.subTest(current=current, target=target):
                self.assertTrue(lifecycle.can_transition(current, target))

    def test_cannot_go_backwards(self) -> None:
        """已核实的工单不能退回待研判——处置是事实，判错了靠批注说明。"""
        for current, target in (("verified", "pending"), ("verified", "tracking"),
                                ("debunked", "verified"), ("tracking", "pending")):
            with self.subTest(current=current, target=target):
                self.assertFalse(lifecycle.can_transition(current, target))

    def test_terminal_states_do_not_move(self) -> None:
        for terminal in ("resolved", "ignored"):
            for target in TicketStatus:
                with self.subTest(terminal=terminal, target=target.value):
                    if target.value == terminal:
                        continue
                    self.assertFalse(lifecycle.can_transition(terminal, target.value))

    def test_any_state_can_be_closed(self) -> None:
        for status in ("pending", "tracking", "verified", "debunked"):
            for target in ("resolved", "ignored"):
                with self.subTest(status=status, target=target):
                    self.assertTrue(lifecycle.can_transition(status, target))

    def test_unknown_status_rejected(self) -> None:
        self.assertFalse(lifecycle.can_transition("pending", "archived"))
        self.assertFalse(lifecycle.can_transition("archived", "pending"))

    def test_closing_statuses_identified(self) -> None:
        self.assertTrue(lifecycle.is_closing("resolved"))
        self.assertTrue(lifecycle.is_closing("ignored"))
        self.assertFalse(lifecycle.is_closing("tracking"))
        self.assertFalse(lifecycle.is_closing("nonsense"))

    def test_transition_error_is_readable(self) -> None:
        message = lifecycle.transition_error("verified", "pending")
        self.assertIn("已核实", message)
        self.assertNotIn("已已", message)
        allowed = message.split("可选：")[-1]
        self.assertIn("辟谣处置", allowed)

    def test_terminal_error_mentions_new_ticket(self) -> None:
        message = lifecycle.transition_error("resolved", "tracking")
        self.assertIn("新建工单", message)


class TicketServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from unittest.mock import patch

        from app.services.auth import passwords

        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.audit_rows: list = []
        from tests.fakes import capture_audit_writes

        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        from tests.fakes import InMemoryEventRepository, make_event

        self.repo = InMemoryTicketRepository()
        self.event_repo = InMemoryEventRepository(events=[make_event(EVENT_ID)],
                                                  links={EVENT_ID: []})
        self.content_repo = InMemoryContentRepository()
        self.analyst = make_user("analyst", role="analyst")
        self.pr = make_user("pr", role="pr")
        self.admin = make_user("admin", role="admin")

    def audit_for(self, action: str) -> list:
        return [row for row in self.audit_rows if row.action == action]

    async def test_create_logs_token_and_first_trace(self) -> None:
        detail = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                            actor=self.analyst, event_id=EVENT_ID,
                                            note="从告警开出")
        self.assertEqual(detail["status"], "pending")
        self.assertEqual(detail["status_label"], "待研判")
        self.assertEqual(len(detail["logs"]), 1)
        self.assertEqual(detail["logs"][0]["action"], "create")
        self.assertEqual(detail["logs"][0]["operator_id"], self.analyst.user_id)

        row = self.audit_for("ticket_flow")[-1]
        self.assertEqual(row.action, "ticket_flow")

    async def test_cannot_open_second_open_ticket_for_same_event(self) -> None:
        await service.create_ticket(self.repo, self.event_repo, self.content_repo, None, actor=self.analyst,
                                    event_id=EVENT_ID)
        with self.assertRaises(AppError) as ctx:
            await service.create_ticket(self.repo, self.event_repo, self.content_repo, None, actor=self.analyst,
                                        event_id=EVENT_ID)

        self.assertEqual(ctx.exception.code, "BIZ_203")
        self.assertIn("已有未关闭的工单", ctx.exception.message)

    async def test_closed_ticket_does_not_block_new_one(self) -> None:
        first = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                            actor=self.analyst, event_id=EVENT_ID)
        await service.update_ticket(self.repo, actor=self.pr,
                                    ticket_id=first["ticket_id"], status="resolved")

        second = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        self.assertNotEqual(second["ticket_id"], first["ticket_id"])

    async def test_unknown_event_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await service.create_ticket(self.repo, self.event_repo, self.content_repo, None, actor=self.analyst,
                                        event_id="evt-none")
        self.assertEqual(ctx.exception.code, "BIZ_201")

    async def test_transition_records_trace_with_note(self) -> None:
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        updated = await service.update_ticket(
            self.repo, actor=self.analyst, ticket_id=created["ticket_id"],
            status="tracking", note="已联系门店核实")

        self.assertEqual(updated["status"], "tracking")
        last = updated["logs"][-1]
        self.assertEqual(last["action"], "claim")             # 首次转入跟踪中 = 认领
        self.assertEqual(last["from_status"], "pending")
        self.assertEqual(last["to_status"], "tracking")
        self.assertEqual(last["note"], "已联系门店核实")
        self.assertEqual(last["action_label"], "认领")

    async def test_comment_without_status_change(self) -> None:
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        updated = await service.update_ticket(
            self.repo, actor=self.analyst, ticket_id=created["ticket_id"],
            note="补充：已完成现场取证")

        self.assertEqual(updated["status"], "pending")        # 状态未变
        self.assertEqual(updated["logs"][-1]["action"], "comment")

    async def test_empty_comment_rejected(self) -> None:
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        with self.assertRaises(AppError) as ctx:
            await service.update_ticket(self.repo, actor=self.analyst,
                                        ticket_id=created["ticket_id"], note="   ")
        self.assertEqual(ctx.exception.code, "BIZ_400")

    async def test_illegal_transition_rejected(self) -> None:
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        await service.update_ticket(self.repo, actor=self.analyst,
                                    ticket_id=created["ticket_id"], status="verified")

        with self.assertRaises(AppError) as ctx:
            await service.update_ticket(self.repo, actor=self.analyst,
                                        ticket_id=created["ticket_id"], status="tracking")
        self.assertEqual(ctx.exception.code, "BIZ_203")

    async def test_closing_requires_pr_role(self) -> None:
        """服务层的第二道检查：路由拦不住时也不能让分析师关单。"""
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)

        with self.assertRaises(AppError) as ctx:
            await service.update_ticket(self.repo, actor=self.analyst,
                                        ticket_id=created["ticket_id"], status="resolved")
        self.assertEqual(ctx.exception.code, "AUTH_003")

        closed = await service.update_ticket(self.repo, actor=self.pr,
                                            ticket_id=created["ticket_id"],
                                            status="resolved", note="已发布澄清声明")
        self.assertEqual(closed["status"], "resolved")
        self.assertIsNotNone(closed["closed_at"])
        self.assertTrue(closed["is_closed"])

    async def test_repeat_same_status_marks_unchanged(self) -> None:
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        detail = await service.update_ticket(self.repo, actor=self.analyst,
                                             ticket_id=created["ticket_id"],
                                             status="pending")

        import json
        row = self.audit_for("ticket_flow")[-1]
        self.assertFalse(json.loads(row.after_value).get("changed", True))

    async def test_detail_includes_trace_in_order(self) -> None:
        created = await service.create_ticket(self.repo, self.event_repo, self.content_repo, None,
                                             actor=self.analyst, event_id=EVENT_ID)
        tid = created["ticket_id"]
        await service.update_ticket(self.repo, actor=self.analyst, ticket_id=tid,
                                    status="tracking", note="开始跟踪")
        await service.update_ticket(self.repo, actor=self.analyst, ticket_id=tid,
                                    note="已取证实物")

        detail = await service.get_ticket_detail(self.repo, tid)
        actions = [log["action"] for log in detail["logs"]]
        self.assertEqual(actions, ["create", "claim", "comment"])
        self.assertEqual([log["operated_at"] for log in detail["logs"]],
                         sorted(log["operated_at"] for log in detail["logs"]))

    async def test_unknown_ticket_returns_404(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await service.get_ticket_detail(self.repo, "tk-none")
        self.assertEqual(ctx.exception.code, "BIZ_201")


class DraftAdoptTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from unittest.mock import patch

        from app.services.auth import passwords

        params_patcher = patch.object(passwords, "PARAMS", {"n": 2 ** 10, "r": 8, "p": 1})
        params_patcher.start()
        self.addCleanup(params_patcher.stop)

        self.audit_rows: list = []
        from tests.fakes import capture_audit_writes

        audit_patcher = capture_audit_writes(self.audit_rows)
        audit_patcher.start()
        self.addCleanup(audit_patcher.stop)

        self.repo = InMemoryTicketRepository([make_ticket("tk1")])
        self.analyst = make_user("analyst", role="analyst")

    async def test_adopt_writes_draft_and_trace(self) -> None:
        detail = await service.adopt_response_draft(
            self.repo, actor=self.analyst, ticket_id="tk1",
            content="【事实澄清】已确认…\n【行动方案】已开通退换通道")

        self.assertTrue(detail["has_response_draft"])
        self.assertIn("事实澄清", detail["response_draft"])
        last = detail["logs"][-1]
        self.assertEqual(last["action"], "draft_adopt")
        self.assertEqual(last["action_label"], "采纳回应草案")

        rows = [row for row in self.audit_rows if row.action == "draft_adopt"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].target, "ticket:tk1")
        self.assertEqual(rows[0].user_id, self.analyst.user_id)

    async def test_adopt_without_content_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await service.adopt_response_draft(self.repo, actor=self.analyst,
                                               ticket_id="tk1")
        self.assertEqual(ctx.exception.code, "BIZ_400")
        self.assertIn("先生成草案", ctx.exception.message)

    async def test_adopt_unknown_ticket_returns_404(self) -> None:
        with self.assertRaises(AppError) as ctx:
            await service.adopt_response_draft(self.repo, actor=self.analyst,
                                               ticket_id="tk-none", content="文本")
        self.assertEqual(ctx.exception.code, "BIZ_201")

    async def test_editable_content_replaces_generated(self) -> None:
        """采纳时传人工编辑后的文本，工单里存的必须是编辑后的版本。"""
        await service.adopt_response_draft(self.repo, actor=self.analyst,
                                           ticket_id="tk1", content="初稿")
        detail = await service.adopt_response_draft(self.repo, actor=self.analyst,
                                                   ticket_id="tk1",
                                                   content="终稿：已与法务确认")
        self.assertEqual(detail["response_draft"], "终稿：已与法务确认")


if __name__ == "__main__":
    unittest.main()
