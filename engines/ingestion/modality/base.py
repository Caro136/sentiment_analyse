"""多模态解析接口。

OCR 与 ASR 的共同抽象。两条不可动摇的约束（设计规格 ADR-06）：

1. **失败绝不阻断**。解析器不得向上抛异常，任何错误都转成 `parse_status=failed`
   并放行——OCR 服务不可用就让整条采集链路停摆是不可接受的，
   内容照常入库、照常打标，只是少了附加文本。

2. **结果是独立证据，不是替换**。解析文本与原文并列进入证据池，
   上层用 `UnifiedContent.analysis_text` 把两者拼接后分析。
    图片里的文字往往正是最关键的信息（现场照片中的标语、视频字幕），
    但它不该覆盖原文。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

# 资产类型常量定义在 contracts/content.py（适配器与解析器共用同一份）
from engines.contracts.content import ASSET_AUDIO, ASSET_IMAGE, ASSET_VIDEO

ASSET_TYPES = (ASSET_IMAGE, ASSET_VIDEO, ASSET_AUDIO)

# 解析状态（与 modality_asset.parse_status 列取值一致）
STATUS_PENDING = "pending"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ModalityInput:
    """待解析的资产。"""

    asset_id: str
    content_uid: str
    asset_type: str
    origin_url: str


@dataclass(frozen=True)
class ModalityOutput:
    """解析产出，可直接落 `modality_asset` 表。"""

    asset_id: str
    content_uid: str
    asset_type: str
    parser: str
    parse_status: str
    parsed_text: str = ""
    error_message: str = ""
    origin_url: str = ""

    @property
    def succeeded(self) -> bool:
        return self.parse_status == STATUS_SUCCESS

    @property
    def has_text(self) -> bool:
        return bool(self.parsed_text.strip())


class ModalityParser(ABC):
    """某种模态的解析器。

    子类必须声明 `parser_name`（落库标识）与 `asset_type`（适用的资产类型）。
    """

    parser_name: ClassVar[str]
    asset_type: ClassVar[str]

    @abstractmethod
    async def parse(self, item: ModalityInput) -> ModalityOutput:
        """解析单个资产。**实现方必须保证不抛异常。**"""

    # ------------------------------------------------------------------
    # 统一的产出构造
    # ------------------------------------------------------------------

    def _success(self, item: ModalityInput, text: str) -> ModalityOutput:
        return ModalityOutput(
            asset_id=item.asset_id,
            content_uid=item.content_uid,
            asset_type=item.asset_type,
            parser=self.parser_name,
            parse_status=STATUS_SUCCESS,
            parsed_text=text,
            origin_url=item.origin_url,
        )

    def _failed(self, item: ModalityInput, error: str) -> ModalityOutput:
        """失败产出。

        错误信息会落库并在界面上展示，因此调用方须先经 `mask_secret` 脱敏——
        OCR 服务的异常里常带着 Authorization 头。
        """
        from engines.common.logging import mask_secret

        return ModalityOutput(
            asset_id=item.asset_id,
            content_uid=item.content_uid,
            asset_type=item.asset_type,
            parser=self.parser_name,
            parse_status=STATUS_FAILED,
            error_message=mask_secret(error)[:500],
            origin_url=item.origin_url,
        )

    @staticmethod
    def make_asset_id(source_type: str, business_id: int, suffix: str) -> str:
        """构造资产业务 ID，保证同一资产重复解析时不变（便于幂等写入）。"""
        return f"{source_type}:{business_id}:{suffix}"
