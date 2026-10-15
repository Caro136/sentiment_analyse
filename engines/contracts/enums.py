"""跨模块共享的枚举。

**取值不是随意定的**——它们必须与两个既有约定严格对齐，否则链路一跑就错位：

1. 数据生成器（`data/generator/scenarios.py`）写入数据库的字面值；
2. 数据库表（`sql/analysis/01_analysis_schema.sql`）注释里声明的取值范围。

改这里的值等于改数据契约，必须同步改数据生成器与 DDL，并重跑 `python -m engines.common.db.check`。
"""

from enum import Enum


class SourceType(str, Enum):
    """四类信源。值与原始表的来源分类、数据生成器的 SOURCE_* 常量一致。"""

    SOCIAL = "social_media"        # 社交媒体（微博）
    VIDEO = "short_video"          # 短视频（抖音）
    NEWS = "news_portal"           # 新闻门户
    FORUM = "industry_forum"       # 行业论坛

    @property
    def label(self) -> str:
        return _SOURCE_LABELS[self]


_SOURCE_LABELS = {
    SourceType.SOCIAL: "社交媒体",
    SourceType.VIDEO: "短视频",
    SourceType.NEWS: "新闻门户",
    SourceType.FORUM: "行业论坛",
}

# 信源 → 原始表名（表名不可推断，必须显式映射）
SOURCE_TABLES: dict[SourceType, dict[str, str]] = {
    SourceType.SOCIAL: {"content": "weibo_note", "comment": "weibo_note_comment"},
    SourceType.VIDEO: {"content": "douyin_aweme", "comment": "douyin_aweme_comment"},
    SourceType.NEWS: {"content": "news_article", "comment": "news_article_comment"},
    SourceType.FORUM: {"content": "forum_thread", "comment": "forum_post"},
}

# 原始表 → 内容表的业务主键字段名（评论表通过它关联内容，**不是主键 id**）
BUSINESS_ID_FIELD: dict[str, str] = {
    "weibo_note": "note_id",
    "douyin_aweme": "aweme_id",
    "news_article": "id",
    "forum_thread": "thread_id",
}

# 原始表 → 发布时间字段（论坛与新闻用 publish_time，社交与短视频用 create_time）
PUBLISH_TIME_FIELD: dict[str, str] = {
    "weibo_note": "create_time",
    "douyin_aweme": "create_time",
    "news_article": "publish_time",
    "forum_thread": "publish_time",
}


class Sentiment(str, Enum):
    """情感倾向三分类。"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"

    @property
    def label(self) -> str:
        return {"positive": "正向", "neutral": "中性", "negative": "负向"}[self.value]


class Emotion(str, Enum):
    """负向情绪的细分标签。正向与中性不细分。

    仅用于负向内容——把「支持」这类正向情绪也塞进同一枚举会让统计口径混乱。
    """

    ANGER = "愤怒"
    DISAPPOINTMENT = "失望"
    PANIC = "恐慌"
    DOUBT = "质疑"


class RiskLevel(str, Enum):
    """风险等级（PRD 附录 B.2）。"""

    S = "S"
    A = "A"
    B = "B"
    C = "C"

    @property
    def label(self) -> str:
        return {"S": "极高危", "A": "高危", "B": "中危", "C": "低危"}[self.value]

    @property
    def severity(self) -> int:
        """数值化严重度，便于排序与比较（S=4 最高）。"""
        return {"S": 4, "A": 3, "B": 2, "C": 1}[self.value]


class ContentRole(str, Enum):
    """内容在传播链路中的角色。

    与数据生成器的角色定义一致，用于传播溯源识别首发与引爆节点。
    """

    ORIGIN = "origin"          # 首发
    IGNITION = "ignition"      # 引爆（KOL / 高权重账号）
    SPREAD = "spread"          # 扩散
    ANALYSIS = "analysis"      # 深度分析（论坛长帖）
    FOLLOWUP = "followup"      # 媒体跟进
    OFFICIAL = "official"      # 官方回应
    AMBIENT = "ambient"        # 背景讨论（与事件弱相关）
    COMMENT = "comment"        # 评论（非内容主体）


class UserRole(str, Enum):
    """平台角色三分类（PRD 第 2 章）。

    取值必须与三处一致：`sys_user.role` 的 DDL 注释、前端 `api/types.ts` 的
    `UserRole`、本枚举。改一处等于改口径，否则角色判断静默失效（表现为「权限不足」
    或「该页永远进不去」）。
    """

    ANALYST = "analyst"    # 舆情分析师
    PR = "pr"              # 公关运营人员
    ADMIN = "admin"        # 系统管理员

    @property
    def label(self) -> str:
        return {
            "analyst": "舆情分析师",
            "pr": "公关运营",
            "admin": "系统管理员",
        }[self.value]


class UserStatus(str, Enum):
    """账号状态。停用后既有会话立即失效（每次请求都会回查账号状态）。"""

    ACTIVE = "active"
    DISABLED = "disabled"

    @property
    def label(self) -> str:
        return {"active": "正常", "disabled": "已停用"}[self.value]


class TopicStatus(str, Enum):
    """监测主题状态（`topic.status`，DDL 注释：enabled/disabled）。"""

    ENABLED = "enabled"
    DISABLED = "disabled"

    @property
    def label(self) -> str:
        return {"enabled": "启用", "disabled": "停用"}[self.value]


class EventStatus(str, Enum):
    """事件专题状态（`event.status`，DDL 注释：monitoring/alerting/handling/resolved）。

    状态流转由处置链路驱动（T-24 预警、T-25 工单）；本任务只读。
    """

    MONITORING = "monitoring"
    ALERTING = "alerting"
    HANDLING = "handling"
    RESOLVED = "resolved"

    @property
    def label(self) -> str:
        return {
            "monitoring": "监测中",
            "alerting": "预警中",
            "handling": "处置中",
            "resolved": "已平息",
        }[self.value]


class AlertRuleType(str, Enum):
    """预警规则类型（`alert_rule.rule_type`，DDL 注释：negative_surge/sensitive_hit/kol_voice）。

    三类对应 PRD 附录 B.4 的三个触发条件，满足任一即告警。
    """

    NEGATIVE_SURGE = "negative_surge"    # 负向声量环比激增
    SENSITIVE_HIT = "sensitive_hit"      # 命中高危敏感词
    KOL_VOICE = "kol_voice"              # 高权重账号发声

    @property
    def label(self) -> str:
        return {
            "negative_surge": "负向声量激增",
            "sensitive_hit": "敏感词命中",
            "kol_voice": "高权重账号发声",
        }[self.value]


class AlertStatus(str, Enum):
    """告警生命周期状态（`alert_record.status`，DDL 注释：unread/read/claimed/closed）。

    流转方向是单向的（见 `engines/alerting/lifecycle.py` 的合法迁移表）：
    未读 → 已读 → 已认领 → 已关闭。
    """

    UNREAD = "unread"
    READ = "read"
    CLAIMED = "claimed"
    CLOSED = "closed"

    @property
    def label(self) -> str:
        return {"unread": "未读", "read": "已读", "claimed": "已认领",
                "closed": "已关闭"}[self.value]


class TicketStatus(str, Enum):
    """处置工单状态（`ticket.status`，DDL 注释：
    pending/tracking/verified/debunked/resolved/ignored）。

    对应 FR-49 的「待研判 → 跟踪中 → 已核实 → 辟谣处置 → 已处理 / 已忽略」。
    `resolved` 与 `ignored` 都是终态（工单关闭），区别在于「做了处置」还是「判定无需处置」。
    """

    PENDING = "pending"        # 待研判
    TRACKING = "tracking"      # 跟踪中
    VERIFIED = "verified"      # 已核实
    DEBUNKED = "debunked"      # 辟谣处置
    RESOLVED = "resolved"      # 已处理
    IGNORED = "ignored"        # 已忽略

    @property
    def label(self) -> str:
        return {
            "pending": "待研判", "tracking": "跟踪中", "verified": "已核实",
            "debunked": "辟谣处置", "resolved": "已处理", "ignored": "已忽略",
        }[self.value]

    @property
    def is_terminal(self) -> bool:
        """终态：已处理 / 已忽略。终态工单不再允许流转。"""
        return self in (TicketStatus.RESOLVED, TicketStatus.IGNORED)


class TicketAction(str, Enum):
    """工单流转记录的动作用值（`ticket_log.action`，DDL 注释：
    create/claim/status_change/comment/draft_adopt）。"""

    CREATE = "create"
    CLAIM = "claim"
    STATUS_CHANGE = "status_change"
    COMMENT = "comment"
    DRAFT_ADOPT = "draft_adopt"

    @property
    def label(self) -> str:
        return {"create": "创建工单", "claim": "认领", "status_change": "状态流转",
                "comment": "批注", "draft_adopt": "采纳回应草案"}[self.value]


class ReportStatus(str, Enum):
    """报告状态（`report.status`，DDL 注释：generating/generated/failed）。"""

    GENERATING = "generating"
    GENERATED = "generated"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {"generating": "生成中", "generated": "已生成", "failed": "生成失败"}[self.value]


class ReportType(str, Enum):
    """报告类型（`report.report_type`，DDL 注释：event专项/periodic周期）。

    专项报告锚定一个事件，周期简报锚定一个主题——两者的区别只体现在
    「以什么为目标」与随后按什么检索，生成链路是同一条。
    """

    EVENT = "event"
    PERIODIC = "periodic"

    @property
    def label(self) -> str:
        return {"event": "事件专项", "periodic": "周期简报"}[self.value]


class KeywordGroup(str, Enum):
    """词包分组（`topic_keyword.group_type`，DDL 注释：brand/event/competitor/exclude）。

    四组的分工是「召回」与「收窄」两类手段：
    品牌词/事件词/竞品词用于圈定监测范围，排除词用于剔除误召回
    （例如品牌词「星耀」会带出「星耀酒店」这类同名噪声时，用排除词压掉）。
    """

    BRAND = "brand"            # 品牌词
    EVENT = "event"            # 事件词
    COMPETITOR = "competitor"  # 竞品词
    EXCLUDE = "exclude"        # 排除词

    @property
    def label(self) -> str:
        return {
            "brand": "品牌词",
            "event": "事件词",
            "competitor": "竞品词",
            "exclude": "排除词",
        }[self.value]

    @property
    def is_recall(self) -> bool:
        """是否为召回类分组。排除词不参与「主题关键词」的选取（见 service 的推导规则）。"""
        return self is not KeywordGroup.EXCLUDE


class AuditAction(str, Enum):
    """需要留痕的关键操作（PRD FR-04）。

    取值与 `audit_log.action` 的 DDL 注释一一对应——**三处必须同时改**：
    本枚举、`sql/business/01_business_schema.sql` 的列注释、以及前端运维页的展示文案
    （`tests/test_permissions.py::AuditActionContractTest` 把这一致性钉死）。

    标「待接」的动作由后续任务调用：装饰器（T-20）已就绪，接上即可，
    不需要改审计模块本身。
    """

    LOGIN = "login"                            # 登录成功（失败也留痕，同一个动作）

    USER_CREATE = "user_create"                # 新增用户（T-19）
    USER_UPDATE = "user_update"                # 改角色 / 状态 / 显示名（T-19）
    USER_RESET_PASSWORD = "user_reset_password"  # 重置口令（T-19）

    CONFIG_UPDATE = "config_update"            # 配置变更：主题词包、通道、预警阈值（待接 T-21/T-24）
    RISK_OVERRIDE = "risk_override"            # 风险等级人工复核（待接 T-23，FR-24）
    ALERT_ACK = "alert_ack"                    # 告警认领与关闭（待接 T-24，FR-56）
    TICKET_FLOW = "ticket_flow"                # 工单创建、状态流转与批注（T-25，FR-49/50）
    DRAFT_ADOPT = "draft_adopt"                # 采纳公关回应草案（T-25，FR-51）
    REPORT_GENERATE = "report_generate"        # 发起报告生成（待接 T-27，FR-58）
    REPORT_EXPORT = "report_export"            # 报告导出（待接 T-27，FR-61）
    DATA_RESET = "data_reset"                  # 数据重置（待接 T-28，FR-75）

    @property
    def label(self) -> str:
        return {
            "login": "登录",
            "user_create": "新增用户",
            "user_update": "修改用户",
            "user_reset_password": "重置口令",
            "config_update": "配置变更",
            "risk_override": "风险复核",
            "alert_ack": "告警认领",
            "ticket_flow": "工单流转",
            "draft_adopt": "采纳回应草案",
            "report_generate": "生成报告",
            "report_export": "导出报告",
            "data_reset": "数据重置",
        }[self.value]


class EvidenceStrength(str, Enum):
    """证据强度：该维度可用于支撑结论的材料充足程度。

    值为 none 时，分析角色应输出占位正文并标记缺失，**不得臆造结论**。
    """

    STRONG = "strong"
    MEDIUM = "medium"
    WEAK = "weak"
    NONE = "none"

    @property
    def label(self) -> str:
        return {"strong": "充分", "medium": "一般", "weak": "不足", "none": "缺失"}[self.value]

    @classmethod
    def from_count(cls, count: int, *, strong: int = 10, medium: int = 5,
                   weak: int = 1) -> "EvidenceStrength":
        """按证据条数分档（阈值与参考实现一致）。"""
        if count >= strong:
            return cls.STRONG
        if count >= medium:
            return cls.MEDIUM
        if count >= weak:
            return cls.WEAK
        return cls.NONE
