"""角色定义：系统中所有「有独立模型配置与日志分流」的角色。

这里是角色的唯一注册处。新增角色时只需在此登记，配置加载（config.llm_for_role）、
日志分流（logging.setup_logging）、研判编排（orchestration 的角色注册表）都会自动识别。

依赖方向：本模块不依赖项目内任何其他模块，可被任意层安全导入。
"""

from dataclasses import dataclass

# 角色标识
ROLE_ANALYTICS = "analytics"        # 认知分析（打标），非 Agent 角色
ROLE_INSIGHT = "insight"            # 私域研究员
ROLE_MEDIA = "media"                # 公域研究员
ROLE_HOST = "host"                  # 主持人研判
ROLE_REPORT = "report"              # 报告引擎


@dataclass(frozen=True)
class RoleInfo:
    """角色元信息。

    key              角色标识（用于事件、日志分流）
    display_name     中文名称（用于进度提示与前端展示）
    config_attr      Settings 上对应的模型配置属性名
    report_dir_attr  Settings 上对应的报告产物目录属性名（非 Agent 角色为 None）
    is_agent         是否参与多智能体研判（决定是否出现在角色注册表中）
    """

    key: str
    display_name: str
    config_attr: str
    report_dir_attr: str | None = None
    is_agent: bool = False

    @property
    def log_file(self) -> str:
        return f"{self.key}.log"


ROLE_INFOS: dict[str, RoleInfo] = {
    ROLE_ANALYTICS: RoleInfo(
        key=ROLE_ANALYTICS,
        display_name="认知分析",
        config_attr="analytics",
        report_dir_attr=None,
        is_agent=False,
    ),
    ROLE_INSIGHT: RoleInfo(
        key=ROLE_INSIGHT,
        display_name="私域研究员",
        config_attr="insight_engine",
        report_dir_attr="insight_report_dir",
        is_agent=True,
    ),
    ROLE_MEDIA: RoleInfo(
        key=ROLE_MEDIA,
        display_name="公域研究员",
        config_attr="media_engine",
        report_dir_attr="media_report_dir",
        is_agent=True,
    ),
    ROLE_HOST: RoleInfo(
        key=ROLE_HOST,
        display_name="主持人研判",
        config_attr="host_agent",
        report_dir_attr="host_report_dir",
        is_agent=True,
    ),
    ROLE_REPORT: RoleInfo(
        key=ROLE_REPORT,
        display_name="报告引擎",
        config_attr="report_engine",
        report_dir_attr="output_dir",
        is_agent=False,
    ),
}

# 参与研判的分析角色（主持人不在其中，它消费前两者的产出）
ANALYSIS_ROLES = (ROLE_INSIGHT, ROLE_MEDIA)


def get_role_info(role_key: str) -> RoleInfo:
    """按角色标识取元信息，未知角色直接报错而非静默返回默认值。"""
    try:
        return ROLE_INFOS[role_key]
    except KeyError:
        known = ", ".join(sorted(ROLE_INFOS))
        raise KeyError(f"未知角色 {role_key!r}，已登记的角色有：{known}") from None
