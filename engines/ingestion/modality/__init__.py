"""多模态解析：OCR 与 ASR。

- `base`       解析器接口与输入输出模型
- `ocr_mcp`    OCR（阿里云百炼 MCP 服务，ADR-07）
- `asr_mock`   ASR 模拟实现（D-05，本期不对接真实语音服务）
- `registry`   按资产类型取解析器
- `repository` modality_asset 表的读写
- `service`    批量解析编排

两条铁律（ADR-06）：**解析失败不阻断入库**；**解析文本是独立证据，不替换原文**。
"""

from engines.ingestion.modality.base import (
    ASSET_AUDIO,
    ASSET_IMAGE,
    ASSET_VIDEO,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCESS,
    ModalityInput,
    ModalityOutput,
    ModalityParser,
)
from engines.ingestion.modality.registry import (
    available_types,
    get_parser,
    register_parser,
    reset_parsers,
)
from engines.ingestion.modality.repository import build_asset_id, load_assets, save_assets
from engines.ingestion.modality.service import (
    ModalityResolution,
    backfill_texts,
    collect_inputs,
    resolve_modality,
)

__all__ = [
    "ASSET_IMAGE", "ASSET_VIDEO", "ASSET_AUDIO",
    "STATUS_PENDING", "STATUS_SUCCESS", "STATUS_FAILED",
    "ModalityInput", "ModalityOutput", "ModalityParser",
    "get_parser", "register_parser", "reset_parsers", "available_types",
    "save_assets", "load_assets", "build_asset_id",
    "resolve_modality", "ModalityResolution", "backfill_texts", "collect_inputs",
]
