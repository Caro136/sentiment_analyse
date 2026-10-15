"""权限矩阵测试（PRD 第 2 章）。

这个矩阵的价值全在「谁明确不可做」上——三类角色在系统里各自都能干很多事，
真正会出错的是放行了不该放行的动作，而且这种错误不会报错，只会让越权操作成功。
因此测试重点是有意**断言否定**：公关不能改主题与阈值、管理员不能碰工单流转、
分析师不能管用户。

`PermissionCoverageTest` 防的是另一种漏子：新增权限点忘了配角色，
结果这个权限谁都用不了（或更糟——被某个兜底逻辑放行）。
"""

import unittest

from engines.contracts.enums import UserRole
from engines.contracts.permissions import (
    OWNED_ONLY,
    PERMISSIONS,
    Permission,
    can,
    in_data_scope,
    permissions_of,
    roles_of,
)


class PrCannotTest(unittest.TestCase):
    """公关运营：PRD 明确「不可修改监测主题与预警阈值」。"""

    def test_cannot_touch_topics(self) -> None:
        self.assertFalse(can(UserRole.PR, Permission.TOPIC_WRITE))

    def test_cannot_touch_channels_or_alert_rules(self) -> None:
        self.assertFalse(can(UserRole.PR, Permission.CHANNEL_WRITE))
        self.assertFalse(can(UserRole.PR, Permission.ALERT_RULE_WRITE))

    def test_cannot_manage_users(self) -> None:
        self.assertFalse(can(UserRole.PR, Permission.USER_MANAGE))

    def test_cannot_override_risk_level(self) -> None:
        """风险复核是分析师的动作；公关只做处置。"""
        self.assertFalse(can(UserRole.PR, Permission.RISK_OVERRIDE))

    def test_cannot_generate_report(self) -> None:
        self.assertFalse(can(UserRole.PR, Permission.REPORT_GENERATE))

    def test_can_do_disposal_work(self) -> None:
        for permission in (Permission.ALERT_READ, Permission.ALERT_ACK,
                           Permission.TICKET_FLOW, Permission.TICKET_CLOSE,
                           Permission.DRAFT_WRITE, Permission.CONTENT_READ,
                           Permission.REPORT_READ, Permission.REPORT_EXPORT):
            with self.subTest(permission=permission.value):
                self.assertTrue(can(UserRole.PR, permission))


class AnalystCannotTest(unittest.TestCase):
    """分析师：不碰运行配置与账号，不做工单最终关闭。"""

    def test_cannot_configure_platform(self) -> None:
        for permission in (Permission.CHANNEL_WRITE, Permission.ALERT_RULE_WRITE,
                           Permission.CONFIG_WRITE):
            with self.subTest(permission=permission.value):
                self.assertFalse(can(UserRole.ANALYST, permission))

    def test_cannot_manage_users_or_read_system_logs(self) -> None:
        self.assertFalse(can(UserRole.ANALYST, Permission.USER_MANAGE))
        self.assertFalse(can(UserRole.ANALYST, Permission.SYSTEM_READ))

    def test_cannot_close_ticket(self) -> None:
        """工单流转可以，最终关闭仅公关（PRD 第 2 章）。"""
        self.assertTrue(can(UserRole.ANALYST, Permission.TICKET_FLOW))
        self.assertFalse(can(UserRole.ANALYST, Permission.TICKET_CLOSE))

    def test_can_read_and_flow_tickets(self) -> None:
        self.assertTrue(can(UserRole.ANALYST, Permission.TICKET_READ))
        self.assertTrue(can(UserRole.ANALYST, Permission.DRAFT_WRITE))

    def test_can_do_analysis_work(self) -> None:
        for permission in (Permission.TOPIC_WRITE, Permission.RISK_OVERRIDE,
                           Permission.RESEARCH_RUN, Permission.REPORT_GENERATE,
                           Permission.CONTENT_READ, Permission.EVENT_READ):
            with self.subTest(permission=permission.value):
                self.assertTrue(can(UserRole.ANALYST, permission))


class AdminCannotTest(unittest.TestCase):
    """管理员：运维与配置，不参与业务处置（PRD「不参与业务研判」）。"""

    def test_cannot_flow_tickets_or_adopt_drafts(self) -> None:
        """管理员不参与业务处置：工单的读、写、关闭都没有（PRD 第 2 章）。"""
        self.assertFalse(can(UserRole.ADMIN, Permission.TICKET_READ))
        self.assertFalse(can(UserRole.ADMIN, Permission.TICKET_FLOW))
        self.assertFalse(can(UserRole.ADMIN, Permission.TICKET_CLOSE))
        self.assertFalse(can(UserRole.ADMIN, Permission.DRAFT_WRITE))

    def test_cannot_ack_alerts(self) -> None:
        self.assertFalse(can(UserRole.ADMIN, Permission.ALERT_ACK))

    def test_cannot_override_risk_level(self) -> None:
        """风险复核仅分析师（PRD 2：管理员不可修改他人主题的业务研判结论）。

        2026-10-15 确认收紧；此前按设计规格 6.2 的示例文案给了管理员，属越权。
        """
        self.assertFalse(can(UserRole.ADMIN, Permission.RISK_OVERRIDE))
        self.assertTrue(can(UserRole.ANALYST, Permission.RISK_OVERRIDE))

    def test_can_configure_and_operate(self) -> None:
        for permission in (Permission.CHANNEL_WRITE, Permission.ALERT_RULE_WRITE,
                           Permission.USER_MANAGE, Permission.CONFIG_WRITE,
                           Permission.SYSTEM_READ, Permission.TOPIC_WRITE):
            with self.subTest(permission=permission.value):
                self.assertTrue(can(UserRole.ADMIN, permission))


class DefaultDenyTest(unittest.TestCase):
    """未知输入一律拒绝——默认拒绝的落点。"""

    def test_unknown_role_denied(self) -> None:
        for role in ("", "root", "superuser", "ANALYST"):
            with self.subTest(role=role):
                self.assertFalse(can(role, Permission.CONTENT_READ))

    def test_unknown_permission_denied(self) -> None:
        # 这些是**当前矩阵里不存在**的权限点（topic:read 等已在 T-21 增设，
        # 因此这里改用确实未登记的资源动作）
        for permission in ("", "topic:delete", "user:read", "*"):
            with self.subTest(permission=permission):
                self.assertFalse(can(UserRole.ADMIN, permission))

    def test_permissions_of_unknown_role_is_empty(self) -> None:
        self.assertEqual(permissions_of("root"), frozenset())

    def test_roles_of_unknown_permission_is_empty(self) -> None:
        self.assertEqual(roles_of("nope:nope"), frozenset())


class PermissionCoverageTest(unittest.TestCase):
    def test_every_permission_is_mapped(self) -> None:
        """每个权限点都必须出现在矩阵里，且至少有一个角色。"""
        for permission in Permission:
            with self.subTest(permission=permission.value):
                self.assertIn(permission, PERMISSIONS)
                self.assertTrue(PERMISSIONS[permission])

    def test_every_role_has_some_permission(self) -> None:
        for role in UserRole:
            with self.subTest(role=role.value):
                self.assertTrue(permissions_of(role))

    def test_read_permissions_are_shared_by_all_roles(self) -> None:
        """读取类权限三方共有：不然会出现「同一份数据有人看不到」。

        `CONFIG_READ` 不在此列——运行配置属于运维面，仅管理员（见矩阵注释）。
        """
        for permission in (Permission.CONTENT_READ, Permission.DASHBOARD_READ,
                           Permission.EVENT_READ, Permission.REPORT_READ,
                           Permission.ALERT_READ, Permission.RESEARCH_READ,
                           Permission.TOPIC_READ, Permission.CHANNEL_READ):
            with self.subTest(permission=permission.value):
                self.assertEqual(PERMISSIONS[permission], frozenset(UserRole))

    def test_permissions_are_immutable(self) -> None:
        """矩阵是 frozenset，运行期改不动（改权限必须改代码并走评审）。"""
        self.assertIsInstance(PERMISSIONS[Permission.USER_MANAGE], frozenset)


class DataScopeTest(unittest.TestCase):
    """数据范围：分析师仅限本人资源（PRD 第 2 章第三级控制）。"""

    def test_analyst_limited_to_own_topics(self) -> None:
        self.assertTrue(in_data_scope(UserRole.ANALYST, Permission.TOPIC_WRITE,
                                      actor_id="u1", owner_id="u1"))
        self.assertFalse(in_data_scope(UserRole.ANALYST, Permission.TOPIC_WRITE,
                                       actor_id="u1", owner_id="u2"))

    def test_admin_not_limited(self) -> None:
        self.assertTrue(in_data_scope(UserRole.ADMIN, Permission.TOPIC_WRITE,
                                      actor_id="admin1", owner_id="u2"))

    def test_new_resource_without_owner_allowed(self) -> None:
        """新建主题时还没有归属，不能因此把创建动作挡掉。"""
        self.assertTrue(in_data_scope(UserRole.ANALYST, Permission.TOPIC_WRITE,
                                      actor_id="u1", owner_id=None))

    def test_unscoped_permissions_ignore_owner(self) -> None:
        for permission in Permission:
            if permission in OWNED_ONLY:
                continue
            with self.subTest(permission=permission.value):
                self.assertTrue(in_data_scope(UserRole.ANALYST, permission,
                                              actor_id="u1", owner_id="u2"))

    def test_unknown_permission_denied(self) -> None:
        self.assertFalse(in_data_scope(UserRole.ADMIN, "nope:nope",
                                       actor_id="a", owner_id=None))


class EnumContractTest(unittest.TestCase):
    """角色取值已冻结：同时与 DDL 注释、前端 types.ts 对齐。"""

    def test_role_values(self) -> None:
        self.assertEqual(UserRole.ANALYST.value, "analyst")
        self.assertEqual(UserRole.PR.value, "pr")
        self.assertEqual(UserRole.ADMIN.value, "admin")

    def test_role_labels_are_chinese(self) -> None:
        for role in UserRole:
            self.assertTrue(role.label)

    def test_matches_frontend_role_type(self) -> None:
        """前端 api/types.ts 的 UserRole 联合类型必须是这三个字面值。"""
        from pathlib import Path

        types_file = Path(__file__).resolve().parents[1] / "front" / "src" / "api" / "types.ts"
        if not types_file.exists():      # 前端工程不在时跳过（后端可独立运行）
            self.skipTest("未找到前端 types.ts")
        content = types_file.read_text(encoding="utf-8")
        expected = "export type UserRole = " + " | ".join(
            f"'{r.value}'" for r in UserRole)
        self.assertIn(expected, content)


class UserStatusTest(unittest.TestCase):
    def test_status_values(self) -> None:
        from engines.contracts.enums import UserStatus

        self.assertEqual(UserStatus.ACTIVE.value, "active")
        self.assertEqual(UserStatus.DISABLED.value, "disabled")


if __name__ == "__main__":
    unittest.main()
