"""分析角色注册表。

新增一个分析角色需要三步（设计规格 3.2 的扩展方式）：

    1. 实现一个 `async def invoke(query) -> Result` 的入口
    2. 在这里登记
    3. 让它在每个章节完成时发布 SECTION_READY 事件

第 3 步是硬性要求：主持人靠这个事件配对研判，
不发事件的角色产出的章节永远进不了研判环节。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from engines.contracts.roles import ROLE_INSIGHT, ROLE_MEDIA, get_role_info


@dataclass(frozen=True)
class ResearchRole:
    """一个可参与研判的分析角色。"""

    key: str
    display_name: str
    invoke: Callable[[str], Awaitable[Any]]

    @property
    def report_dir_attr(self) -> str | None:
        return get_role_info(self.key).report_dir_attr


def _build_registry() -> dict[str, ResearchRole]:
    """惰性构建注册表。

    入口函数在函数内部导入：`insight_agent` 与 `media_agent` 都会导入本模块的
    兄弟模块，在模块顶层导入会形成环。
    """
    from engines.insight_agent import run_insight_agent
    from engines.media_agent import run_media_agent

    return {
        ROLE_INSIGHT: ResearchRole(
            key=ROLE_INSIGHT,
            display_name=get_role_info(ROLE_INSIGHT).display_name,
            invoke=run_insight_agent,
        ),
        ROLE_MEDIA: ResearchRole(
            key=ROLE_MEDIA,
            display_name=get_role_info(ROLE_MEDIA).display_name,
            invoke=run_media_agent,
        ),
    }


_registry: dict[str, ResearchRole] | None = None


def get_registry() -> dict[str, ResearchRole]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


def resolve_roles(keys: list[str] | None = None) -> list[ResearchRole]:
    """解析要运行的角色。默认全部；未知角色直接报错而不是静默跳过。"""
    registry = get_registry()
    if not keys:
        return list(registry.values())

    resolved = []
    for key in keys:
        role = registry.get(key)
        if role is None:
            known = ", ".join(sorted(registry))
            raise KeyError(f"未知分析角色 {key!r}，已注册：{known}")
        resolved.append(role)
    return resolved


def role_names() -> list[str]:
    return sorted(get_registry())
