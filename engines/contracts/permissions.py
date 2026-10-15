"""权限矩阵：角色 × 功能 × 数据范围（PRD 第 2 章 / 设计规格 8.1）。

**为什么把矩阵放进 contracts**：鉴权发生在三处——接入层的接口依赖、服务层的用例校验、
以及将来可能的运维脚本。三处各写一份角色判断必然漂移（典型症状：接口放行了、
服务层又拦一次，或者反过来漏拦）。矩阵只在这里定义一次，其余地方一律调用 `can()`。

矩阵依据逐条对应 PRD 第 2 章的三段职责描述，`PERMISSIONS` 的值即「谁可以」：

- 分析师：本人主题的增改停、全量舆情流、发起研判、风险复核、发起并导出报告
- 公关：全量舆情流与预警、传播脉络与观点、回应草案、工单流转、**工单关闭仅公关**
- 管理员：通道与频率、预警阈值与通知渠道、用户与角色、系统日志与任务状态

数据范围（第三级）单独表达：`OWNED_ONLY` 里的权限对分析师只作用于**本人**的资源，
判定入口是 `in_data_scope()`。资源归属由调用方（服务层）提供——矩阵不查库。

**默认拒绝**：未在 `PERMISSIONS` 中列出的权限点，任何角色都没有；
新增权限点时若漏配角色，结果是「谁都进不去」（显式失败），而不是「谁都能进」。
"""

from enum import Enum

from .enums import UserRole

__all__ = [
    "Permission",
    "PERMISSIONS",
    "OWNED_ONLY",
    "SCOPED_ROLES",
    "can",
    "permissions_of",
    "roles_of",
    "in_data_scope",
]


class Permission(str, Enum):
    """权限点。命名规则：`资源:动作`。

    前六个为 PRD 第 2 章中「某一方明确不可做」的动作，是权限矩阵的真正价值所在；
    后半段是三方共有的读取与处置类动作，列出来是为了让「默认拒绝」有据可依
    ——不在本枚举里的动作，接口层也必须显式声明才可放行。
    """

    # ---- 监测配置（PRD 2：公关不可修改主题与阈值）----
    TOPIC_WRITE = "topic:write"                  # 主题与词包增改停（分析师限本人）
    CHANNEL_WRITE = "channel:write"              # 信源通道开关与采集频率
    ALERT_RULE_WRITE = "alert_rule:write"        # 预警分级阈值与通知渠道

    # ---- 账号与运行（PRD 2：仅管理员）----
    USER_MANAGE = "user:manage"                  # 新增/停用用户、重置口令、调整角色（FR-03）
    CONFIG_WRITE = "config:write"                # 运行配置热更新白名单（FR-53）
    SYSTEM_READ = "system:read"                  # 系统日志与任务状态（FR-72~75）

    # ---- 舆情读取（三方共有）----
    TOPIC_READ = "topic:read"                    # 主题与词包列表（筛选、大盘按主题聚合都要）
    CHANNEL_READ = "channel:read"                # 信源通道状态与连通性自检（FR-11/13）
    CONFIG_READ = "config:read"                  # 运行配置读取（运维面，仅管理员）
    CONTENT_READ = "content:read"                # 舆情动态流与详情（FR-42~44）
    DASHBOARD_READ = "dashboard:read"            # 态势大盘（FR-36~41）
    ALERT_READ = "alert:read"                    # 告警列表（FR-56）
    EVENT_READ = "event:read"                    # 事件专题、溯源、聚类、图谱（FR-45~48）
    RESEARCH_READ = "research:read"              # 研判结果与讨论（FR-64~66）
    REPORT_READ = "report:read"                  # 报告列表与预览（FR-57/60）

    # ---- 业务写动作 ----
    RISK_OVERRIDE = "risk:override"              # 风险等级人工复核（FR-24）
    RESEARCH_RUN = "research:run"                # 发起多智能体研判（FR-64）
    ALERT_ACK = "alert:ack"                      # 告警认领与关闭（FR-56）
    TICKET_READ = "ticket:read"                  # 查看处置工单与处置轨迹（FR-50）
    REPORT_GENERATE = "report:generate"          # 发起报告生成（FR-58）
    REPORT_EXPORT = "report:export"              # 报告导出（FR-61）
    TICKET_FLOW = "ticket:flow"                  # 工单状态流转与批注（FR-49/50）
    TICKET_CLOSE = "ticket:close"                # 工单最终关闭（PRD 2：仅公关）
    DRAFT_WRITE = "draft:write"                  # 公关回应草案的编辑与采纳（FR-51）

    @property
    def label(self) -> str:
        return _PERMISSION_LABELS.get(self, self.value)


_PERMISSION_LABELS: dict[Permission, str] = {
    Permission.TOPIC_WRITE: "监测主题维护",
    Permission.CHANNEL_WRITE: "信源通道配置",
    Permission.ALERT_RULE_WRITE: "预警规则配置",
    Permission.USER_MANAGE: "用户与角色管理",
    Permission.CONFIG_WRITE: "运行配置维护",
    Permission.SYSTEM_READ: "系统运维查看",
    Permission.TOPIC_READ: "查看监测主题",
    Permission.CHANNEL_READ: "查看信源通道",
    Permission.CONFIG_READ: "查看运行配置",
    Permission.CONTENT_READ: "查看舆情流",
    Permission.DASHBOARD_READ: "查看态势大盘",
    Permission.ALERT_READ: "查看告警",
    Permission.EVENT_READ: "查看事件专题",
    Permission.RESEARCH_READ: "查看研判结果",
    Permission.REPORT_READ: "查看报告",
    Permission.RISK_OVERRIDE: "风险等级复核",
    Permission.RESEARCH_RUN: "发起研判",
    Permission.ALERT_ACK: "告警认领",
    Permission.TICKET_READ: "查看处置工单",
    Permission.REPORT_GENERATE: "生成报告",
    Permission.REPORT_EXPORT: "导出报告",
    Permission.TICKET_FLOW: "工单流转",
    Permission.TICKET_CLOSE: "工单关闭",
    Permission.DRAFT_WRITE: "回应草案采纳",
}

_ALL = frozenset(UserRole)

# 权限点 → 可执行的角色集合
PERMISSIONS: dict[Permission, frozenset[UserRole]] = {
    # 公关明确不可：主题与阈值（PRD 2）；分析师仅限本人主题（见 OWNED_ONLY）
    Permission.TOPIC_WRITE: frozenset({UserRole.ANALYST, UserRole.ADMIN}),
    Permission.CHANNEL_WRITE: frozenset({UserRole.ADMIN}),
    Permission.ALERT_RULE_WRITE: frozenset({UserRole.ADMIN}),

    # 管理员专属：账号与运行
    Permission.USER_MANAGE: frozenset({UserRole.ADMIN}),
    Permission.CONFIG_WRITE: frozenset({UserRole.ADMIN}),
    # 运行配置属于运维面：能读就等于能看到平台的可调参数与当前取值，
    # 没必要给业务角色看（PRD 第 2 章：运行配置是管理员的职责）
    Permission.CONFIG_READ: frozenset({UserRole.ADMIN}),
    Permission.SYSTEM_READ: frozenset({UserRole.ADMIN}),

    # 三方共有：读取
    Permission.TOPIC_READ: _ALL,
    Permission.CHANNEL_READ: _ALL,
    Permission.CONTENT_READ: _ALL,
    Permission.DASHBOARD_READ: _ALL,
    Permission.ALERT_READ: _ALL,
    Permission.EVENT_READ: _ALL,
    Permission.RESEARCH_READ: _ALL,
    Permission.REPORT_READ: _ALL,

    # 分析师主导的研判动作。
    # 风险复核**仅分析师**：PRD 第 2 章称管理员「不可修改他人主题的业务研判结论」，
    # 2026-10-15 确认按此收紧（设计规格 6.2 的示例原先写作「仅分析师与管理员」，
    # 已同步修正）。管理员要纠错，走「改口令/停用账号」这类运行手段，不碰业务结论。
    Permission.RISK_OVERRIDE: frozenset({UserRole.ANALYST}),
    Permission.RESEARCH_RUN: frozenset({UserRole.ANALYST, UserRole.ADMIN}),
    Permission.ALERT_ACK: frozenset({UserRole.ANALYST, UserRole.PR}),
    Permission.REPORT_GENERATE: frozenset({UserRole.ANALYST, UserRole.ADMIN}),
    Permission.REPORT_EXPORT: _ALL,

    # 处置动作：分析师与公关共有的写入（PRD 2），但工单关闭仅公关。
    # 读取也限这两个角色：管理员「不参与业务处置」，PRD 第 2 章没有给它工单能力。
    Permission.TICKET_READ: frozenset({UserRole.ANALYST, UserRole.PR}),
    Permission.TICKET_FLOW: frozenset({UserRole.ANALYST, UserRole.PR}),
    Permission.TICKET_CLOSE: frozenset({UserRole.PR}),
    Permission.DRAFT_WRITE: frozenset({UserRole.ANALYST, UserRole.PR}),
}

# 数据范围受限的权限点：分析师只能作用于本人资源；管理员不受限
OWNED_ONLY: frozenset[Permission] = frozenset({Permission.TOPIC_WRITE})

# 受「本人资源」约束的角色。与 OWNED_ONLY 是两个维度——前者是哪些动作受限，
# 后者是谁受限。只有分析师是「主题归属数据」，管理员与公关是全量数据（PRD 第 2 章）。
SCOPED_ROLES: frozenset[UserRole] = frozenset({UserRole.ANALYST})


def can(role: UserRole | str, permission: Permission | str) -> bool:
    """角色是否具备某权限点（不含数据范围判定）。

    未知角色或未登记的权限点一律返回 False——默认拒绝。
    """
    try:
        role_enum = UserRole(role)
    except ValueError:
        return False

    try:
        perm_enum = Permission(permission)
    except ValueError:
        return False

    return role_enum in PERMISSIONS.get(perm_enum, frozenset())


def permissions_of(role: UserRole | str) -> frozenset[Permission]:
    """该角色的全部权限点，供前端菜单裁剪与自检使用。"""
    try:
        role_enum = UserRole(role)
    except ValueError:
        return frozenset()
    return frozenset(p for p, roles in PERMISSIONS.items() if role_enum in roles)


def roles_of(permission: Permission | str) -> frozenset[UserRole]:
    """某权限点允许的角色集合。"""
    try:
        perm_enum = Permission(permission)
    except ValueError:
        return frozenset()
    return PERMISSIONS.get(perm_enum, frozenset())


def in_data_scope(role: UserRole | str, permission: Permission | str, *,
                  actor_id: str, owner_id: str | None) -> bool:
    """数据范围判定：本人 vs 全量（PRD 第 2 章的第三级控制）。

    同时满足「权限点在 `OWNED_ONLY` 里」与「角色在 `SCOPED_ROLES` 里」才做归属比较：
    分析师改他人的主题要被拦，管理员改任何人的主题是本职（PRD 第 2 章：管理员
    可配置平台，不受主题归属限制）。少判角色这一层，就会出现「管理员改不了任何主题」
    ——权限矩阵看着没错，实际操作全被拒。

    `owner_id` 为 None 表示资源尚无归属（新建场景），此时放行。
    """
    try:
        perm_enum = Permission(permission)
        role_enum = UserRole(role)
    except ValueError:
        return False

    if perm_enum not in OWNED_ONLY or role_enum not in SCOPED_ROLES:
        return True
    if owner_id is None:
        return True
    return actor_id == owner_id
