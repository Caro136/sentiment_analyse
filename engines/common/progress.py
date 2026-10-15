"""进度回调：Agent 向编排层上报进度的**中立结构**。

为什么单独定义这个结构，而不是让 Agent 直接发事件：

    Agent 不该感知事件系统。它只接收一个 `progress` 回调并调用它，
    至于编排层把回调结果转成 `RoleProgressEvent` 还是写数据库，Agent 不关心。

好处是 Agent 可以脱离 eventing 单独测试（传一个收集列表的回调即可）。

用法：
    # Agent 内
    def run(..., progress: ProgressCallback | None = None) -> ...:
        _report(progress, Stage.PLANNING, "正在规划分析维度")

    # 编排层
    lambda update: publish_role_progress(role, update.status, update.message, update.progress_pct)
"""

from collections.abc import Callable
from dataclasses import dataclass

# 进度阶段 → (起始百分比, 结束百分比)
# 区间刻意留空档（40~50、60~70、80~90）：章节循环的耗时不可预测，
# 留白让进度条在长阶段不至于长时间停在同一个数值上。
STAGE_RANGES: dict[str, tuple[int, int]] = {
    "planning": (10, 20),
    "retrieving": (20, 30),
    "searching": (30, 40),
    "summary": (50, 60),
    "formatting": (70, 80),
    "completed": (90, 100),
}


@dataclass(frozen=True)
class ProgressUpdate:
    """一次进度上报。

    status       阶段标识（见 STAGE_RANGES 的键）
    message      面向用户的说明文字
    progress_pct 总体进度百分比 0~100
    """

    status: str
    message: str = ""
    progress_pct: int = 0

    def __post_init__(self) -> None:
        clamped = max(0, min(100, self.progress_pct))
        if clamped != self.progress_pct:
            object.__setattr__(self, "progress_pct", clamped)


# 进度回调签名
ProgressCallback = Callable[[ProgressUpdate], None]


def stage_percent(status: str, ratio: float = 0.0) -> int:
    """把「阶段 + 阶段内比例」换算成总体百分比。

    >>> stage_percent("summary", 0.5)   # 章节生成到一半
    55
    """
    start, end = STAGE_RANGES.get(status, (0, 100))
    ratio = max(0.0, min(1.0, ratio))
    return int(start + (end - start) * ratio)


def report(progress: ProgressCallback | None, status: str, message: str = "",
           *, ratio: float = 0.0, percent: int | None = None) -> None:
    """安全上报：progress 为 None 时静默跳过。

    Agent 内部一律通过本函数上报，避免到处写 `if progress: progress(...)`。
    """
    if progress is None:
        return
    pct = percent if percent is not None else stage_percent(status, ratio)
    progress(ProgressUpdate(status=status, message=message, progress_pct=pct))
