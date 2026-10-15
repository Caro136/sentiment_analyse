"""模拟 ASR 解析器。

**本期不对接真实语音识别服务**（设计规格 D-05）。接口形状做真，便于将来替换实现——
换真实服务时只需新增一个 `AsrParser` 并改注册表，调用方无感。

为什么保留这个模拟实现而不是干脆不做：接入链路需要验证「多模态文本如何汇入证据池」
这条路径本身，没有 ASR 产出就测不到。但**演示时必须明确标注这是模拟数据**，
不得表述为具备真实语音转写能力。
"""

from collections.abc import Mapping
from typing import ClassVar

from engines.ingestion.modality.base import (
    ASSET_AUDIO,
    ASSET_VIDEO,
    ModalityInput,
    ModalityOutput,
    ModalityParser,
)

# 未命中预置样本时的占位文本。刻意写得像"未实现"而不是像"转录结果"，
# 避免演示时被误认为真实语音识别产出。
PLACEHOLDER_TEXT = "[模拟 ASR] 未接入真实语音识别服务，此处为占位转录文本"


class MockAsrParser(ModalityParser):
    """模拟音视频转录。

    `samples` 用于注入预置转录文本（{content_uid: 文本}）：
    联调与测试时需要内容确定的转录结果，没有它就只能拿到占位串。
    """

    parser_name: ClassVar[str] = "asr_mock"
    asset_type: ClassVar[str] = ASSET_VIDEO

    def __init__(self, samples: Mapping[str, str] | None = None) -> None:
        self._samples = dict(samples or {})

    async def parse(self, item: ModalityInput) -> ModalityOutput:
        if item.asset_type not in (ASSET_VIDEO, ASSET_AUDIO):
            return self._failed(item, f"模拟 ASR 不支持资产类型 {item.asset_type}")

        text = self._samples.get(item.content_uid, PLACEHOLDER_TEXT)
        return self._success(item, text)

    def register_sample(self, content_uid: str, text: str) -> None:
        """登记一条预置转录文本。"""
        self._samples[content_uid] = text
