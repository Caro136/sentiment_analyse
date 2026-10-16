"""研判任务的请求响应模型（FR-64~66）。

与设计规格 6.1 的 API-22/23 对应：
  - `POST /api/v1/research` → 202 `{task_id, started, ...}`
  - `GET  /api/v1/research/{task_id}` → 任务状态与各角色结果
"""

from typing import Any

from pydantic import BaseModel, Field

__all__ = [
    "ResearchStartRequest",
    "ResearchStartView",
    "ResearchTaskView",
]

# 可指定的分析角色。主持人不在其中——它由编排层按「先启动」的顺序自己管理，
# 让调用方决定要不要主持人，等于把硬约束（主持人必须先起）交给调用方去遵守。
RESEARCH_ROLES = ("insight", "media")


class ResearchStartRequest(BaseModel):
    """发起一次研判。

    目标二选一：`query`（直接给主题词，同时是三方产物的加载键）或 `topic_id`
    （由后端取主题名称作为 query）。两者都给时以 `query` 为准。
    """

    query: str = Field(default="", max_length=128,
                       description="研判主题词；与 topic_id 二选一")
    topic_id: str | None = Field(default=None, max_length=64,
                                 description="监测主题；留空时必须给 query")
    roles: list[str] = Field(default_factory=lambda: list(RESEARCH_ROLES),
                             description="参与的分析角色，默认全部")


class ResearchStartView(BaseModel):
    """发起结果。`started=true` 表示任务已受理，进度经 SSE 推送（FR-69）。"""

    task_id: str
    started: bool = True
    query: str = ""
    status: str = ""
    roles: list[str] = Field(default_factory=list)


class ResearchTaskView(BaseModel):
    """研判任务状态与各角色结果（FR-66）。"""

    task_id: str
    query: str
    status: str
    duration: float = 0.0
    host_path: str = Field(default="", description="主持人最终裁判的产物路径")
    error: str = Field(default="", description="任务级失败原因（角色级在 results 里）")
    results: dict[str, Any] = Field(default_factory=dict)
