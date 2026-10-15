"""多模态解析器注册表。

按资产类型取解析器。当前实现：
  - image → OcrMcpParser（阿里云百炼 MCP 服务）
  - video / audio → MockAsrParser（模拟，见 D-05）

将来接入真实语音服务时，新增一个解析器并在此登记即可，调用方无需改动。
"""

from collections.abc import Callable

from engines.contracts.content import ASSET_AUDIO, ASSET_IMAGE, ASSET_VIDEO
from engines.ingestion.modality.asr_mock import MockAsrParser
from engines.ingestion.modality.base import ModalityParser
from engines.ingestion.modality.ocr_mcp import OcrMcpParser

# 工厂而非实例：解析器可能持有连接或配置，惰性创建
_FACTORIES: dict[str, Callable[[], ModalityParser]] = {
    ASSET_IMAGE: OcrMcpParser,
    ASSET_VIDEO: MockAsrParser,
    ASSET_AUDIO: MockAsrParser,
}

_instances: dict[str, ModalityParser] = {}


def get_parser(asset_type: str) -> ModalityParser:
    """按资产类型取解析器（单例）。"""
    if asset_type not in _instances:
        factory = _FACTORIES.get(asset_type)
        if factory is None:
            known = ", ".join(sorted(_FACTORIES))
            raise KeyError(f"资产类型 {asset_type!r} 没有可用的解析器，已支持：{known}")
        _instances[asset_type] = factory()
    return _instances[asset_type]


def register_parser(asset_type: str, parser: ModalityParser) -> None:
    """替换某类型的解析器（测试注入用）。"""
    _FACTORIES[asset_type] = lambda: parser
    _instances[asset_type] = parser


def reset_parsers() -> None:
    """清空已创建的实例，下次取用时重新构造（配置变更或测试隔离时使用）。"""
    _instances.clear()


def available_types() -> tuple[str, ...]:
    return tuple(sorted(_FACTORIES))
