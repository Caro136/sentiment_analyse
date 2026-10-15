"""章节草稿：两个分析角色的共同产出结构。

放在 contracts 而不是各自的 Agent 包里，是因为私域与公域研究员产出的章节
**必须能被主持人放在一起比较**（按 section_key 配对）。结构相同是配对的前提，
各自定义一份迟早会漂移——比如一边加了 `strength` 字段、另一边没加，
主持人读取时就会得到 None。

注意这不违反「两个 Agent 互不依赖」：它们都依赖 contracts，但彼此不依赖。
"""

from dataclasses import dataclass, field


@dataclass
class SectionDraft:
    """一个已生成的章节。"""

    section_key: str
    index: int
    title: str
    body: str = ""
    evidence_count: int = 0
    strength: str = ""
    is_fallback: bool = False        # 证据不足时输出的占位正文
    key_findings: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        return f"## {self.title}\n\n{self.body}\n"

    @property
    def has_content(self) -> bool:
        return bool(self.body.strip()) and not self.is_fallback
