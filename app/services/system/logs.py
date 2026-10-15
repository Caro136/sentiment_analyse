"""运行日志检索（FR-73）。

日志按角色分流写在 `logs/` 下（`app.log` + 各角色日志，见 `common/logging.py`），
这里按「模块（日志文件）/ 时间范围 / 关键字 / trace_id」检索**尾部**若干行。

三个实现要点：

1. **只读文件尾部**。日志是追加写的、动辄几十兆，全量读进来再过滤会把内存吃掉；
   这里从文件末尾按块回读，攒够 `limit` 条就停。
2. **返回前统一脱敏**（`mask_secret`）。日志写入时已做过脱敏，但**第三方库
   （httpx、sqlalchemy）自己打印的内容不受我们控制**——实测 httpx 会把含查询参数
   的完整 URL 打进 INFO 日志。读出口再兜一道，代价极小。
3. **不提供写/删日志的接口**。日志是排障材料，运维页只需能查；
   给一个删除入口迟早会被误用，而且删掉的正是案发现场。
"""

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from engines.common.logging import LOG_DIR, mask_secret
from engines.contracts.roles import ROLE_INFOS

__all__ = [
    "LOG_MODULES",
    "LogLine",
    "LogSearchResult",
    "search_logs",
    "available_modules",
    "READ_CHUNK_BYTES",
]

# 可检索的模块：app（全量归档）+ 各角色日志。取值即 `logs/{module}.log`
LOG_MODULES = ("app", *ROLE_INFOS.keys())

# 从文件末尾回读的块大小。64KB 一块，够装几百行；文件再大也不会一次性读进内存
READ_CHUNK_BYTES = 64 * 1024

# 单次检索的最大返回行数（前端分页由 limit 控制，不做 offset——日志只看最近）
MAX_LIMIT = 500

# 日志行形如 `2026-10-15 20:03:56.036 | INFO | role | trace | 模块:函数:行 | 消息`
_LINE_START = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3} \| "
                         r"(?P<level>[A-Z]+)\s*\|")


@dataclass(frozen=True)
class LogLine:
    module: str
    raw: str
    timestamp: str = ""
    level: str = ""

    def to_dict(self) -> dict:
        return {"module": self.module, "timestamp": self.timestamp,
                "level": self.level, "text": self.raw}


@dataclass
class LogSearchResult:
    module: str
    matched: int = 0
    scanned: int = 0
    truncated: bool = False
    lines: list[LogLine] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.lines is None:
            self.lines = []


def available_modules() -> list[dict]:
    """可检索的模块及其文件现状（大小、是否存在）。"""
    items = []
    for module in LOG_MODULES:
        path = LOG_DIR / f"{module}.log"
        items.append({
            "module": module,
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        })
    return items


def _iter_tail_lines(path: Path, *, block_bytes: int = READ_CHUNK_BYTES):
    """从文件末尾向前按块回读，产出**逆序**的完整行。"""
    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        pending = b""
        while position > 0:
            size = min(block_bytes, position)
            position -= size
            handle.seek(position)
            chunk = handle.read(size) + pending
            parts = chunk.split(b"\n")
            # 第一段可能被截断（它的前半在更靠前的块里），留到下一轮拼接
            pending = parts.pop(0)
            for line in reversed(parts):
                yield line
        if pending:
            yield pending


def _parse_line(module: str, raw: str) -> LogLine:
    matched = _LINE_START.match(raw)
    if not matched:
        return LogLine(module=module, raw=raw)
    return LogLine(module=module, raw=raw, timestamp=matched.group("ts"),
                   level=matched.group("level"))


def search_logs(*, module: str = "app", keyword: str | None = None,
                level: str | None = None, trace_id: str | None = None,
                start_time: str | None = None, end_time: str | None = None,
                limit: int = 100, log_dir: Path | None = None) -> LogSearchResult:
    """检索日志尾部，返回**最近的匹配行**（结果按时间正序，便于阅读）。

    `start_time` / `end_time` 用 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM:SS` 字符串比较——
    日志行的时间戳就是同格式文本，字典序即时间序，不必解析成时间对象。
    """
    root = log_dir or LOG_DIR
    path = root / f"{module}.log"
    result = LogSearchResult(module=module)
    if not path.is_file():
        return result

    limit = max(1, min(limit, MAX_LIMIT))
    keyword = (keyword or "").strip() or None
    level = (level or "").strip().upper() or None

    matched: list[LogLine] = []
    scanned = 0
    for raw_bytes in _iter_tail_lines(path):
        raw = raw_bytes.decode("utf-8", errors="replace").rstrip("\r")
        if not raw.strip():
            continue
        scanned += 1
        line = _parse_line(module, raw)

        if level and line.level != level:
            continue
        if keyword and keyword not in raw:
            continue
        if trace_id and trace_id not in raw:
            continue
        if start_time and line.timestamp and line.timestamp < start_time:
            # 日志按时间正序写入，越往前越早 → 早于起点即可停止回读
            break
        if end_time and line.timestamp and line.timestamp > end_time:
            continue

        # 读出口统一脱敏：第三方库打印的内容不受我们的写入脱敏控制
        matched.append(LogLine(module=module, raw=mask_secret(raw),
                               timestamp=line.timestamp, level=line.level))
        if len(matched) >= limit:
            result.truncated = True
            break

    result.matched = len(matched)
    result.scanned = scanned
    result.lines = list(reversed(matched))       # 回读是逆序，翻回正序
    logger.debug("日志检索 module={} 命中 {} 行（扫描 {} 行）", module, result.matched, scanned)
    return result
