"""主持人入口。

对外只暴露「启动 / 停止 / 查看讨论记录」三件事——
主持人不是被主动调用的，它**监听**两个研究员的产出自己动起来。
"""

from dataclasses import dataclass, field

from engines.common.eventing.bus import reset_event_bus
from engines.common.logging import logger
from engines.contracts.section import SectionDraft
from engines.host_agent.listener import SectionReadyListener
from engines.host_agent.state import HostSession


@dataclass
class HostStatus:
    """主持人运行时状态。"""

    running: bool = False
    query: str = ""
    received: dict = field(default_factory=dict)     # 维度 → 已到达的来源
    judged: list[str] = field(default_factory=list)
    pending: int = 0
    processed: int = 0
    complete: bool = False
    report_path: str = ""

    def render(self) -> str:
        return (f"主持人{'运行中' if self.running else '未运行'} | "
                f"主题 {self.query or '(未开始)'} | "
                f"已研判 {len(self.judged)}/5 维 | "
                f"已处理事件 {self.processed} | "
                f"{'已完成' if self.complete else '进行中'}")


class HostAgent:
    """主持人的启停与状态查询。

    用法：
        host = HostAgent()
        host.start("星耀XPhone15")      # 开始监听
        ...                             # 两个研究员跑完，事件自动驱动研判
        status = host.status()          # 查看进度
        await host.stop()               # 结束并清空会话
    """

    def __init__(self) -> None:
        self.session = HostSession()
        self._listener = SectionReadyListener(self.session)

    def start(self, query: str) -> None:
        """开始监听指定主题的章节事件。"""
        if self._listener.running:
            logger.warning("主持人已在运行，先停止再启动")
            return
        self.session.clear()
        self.session.query = query
        self.session.pairs.query = query
        self._listener.start()

    async def stop(self) -> None:
        """停止监听并清空会话。"""
        await self._listener.stop()

    @property
    def running(self) -> bool:
        return self._listener.running

    def status(self) -> HostStatus:
        pairs = self.session.pairs
        return HostStatus(
            running=self._listener.running,
            query=self.session.query,
            received={k: sorted(v) for k, v in pairs.results.items()},
            judged=sorted(pairs.judged),
            pending=self._listener.pending,
            processed=self._listener.processed,
            complete=self.session.is_complete,
            report_path=self.session.report_path,
        )

    def judgements(self) -> list:
        """按五维顺序返回研判结论。"""
        return self.session.ordered_judgements()

    def discussion_records(self) -> list[dict]:
        """讨论区记录：五维研判 + 最终裁判，供前端轮询。

        结构与事件驱动的展示保持一致——前端不必区分"从事件流拿到的"与
        "从轮询拿到的"，两者格式相同。
        """
        records = [
            {
                "source": "host",
                "dimension_key": judgement.section_key,
                "dimension_title": judgement.title,
                "message_text": judgement.summary,
                "consensus": judgement.consensus,
                "divergence": judgement.divergence,
                "risks": judgement.risks,
                "is_final": False,
            }
            for judgement in self.session.ordered_judgements()
        ]
        if self.session.final_report:
            records.append({
                "source": "host",
                "dimension_key": None,
                "dimension_title": "最终裁判",
                "message_text": self.session.final_report,
                "is_final": True,
                "confidence": self.session.final_confidence,
            })
        return records


def reset_runtime() -> None:
    """重置运行时（进程内单例的清理入口，测试与会话切换时用）。"""
    reset_event_bus()
    logger.info("主持人运行时已重置")


__all__ = ["HostAgent", "HostStatus", "HostSession", "SectionDraft", "reset_runtime"]
