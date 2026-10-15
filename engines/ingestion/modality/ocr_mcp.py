"""OCR 解析器：通过 MCP 协议接入云端服务（设计规格 ADR-07）。

**两条纪律**（附录 C.2）：

1. **先 `list_tools()` 再 `call_tool()`，不硬编码工具名与参数名**。
   MCP 服务的工具签名只有连上才知道；凭猜测写参数名，最坏情况是服务静默返回错误结果
   而不是报错——那比直接失败更难排查。这里从 schema 里选参数名，选不中就用唯一参数。

2. **任何异常都转成 failed 产出，绝不向上抛**。
   OCR 是增强项而非必需项，它挂了不该让内容入库停摆（ADR-06）。

关于演示数据：数据集里的图片地址是占位 URL（`cover.example-cdn.com`），真实调用会失败。
这不是缺陷——它正好演示了「解析失败不阻断入库」这条设计。要验证真实识别能力，
需传入可访问的图片地址（见 `tests/test_modality.py` 的手动验证用例）。
"""

import asyncio
from typing import Any, ClassVar

from engines.common.logging import logger, mask_secret
from engines.contracts.config import get_settings
from engines.ingestion.modality.base import (
    ASSET_IMAGE,
    ModalityInput,
    ModalityOutput,
    ModalityParser,
)

# 参数名候选：不同 OCR 服务的入参命名不一致，按 schema 自适应
_PARAM_CANDIDATES = ("image_url", "image", "url", "img_url", "image_path", "file_url")


def _require_mcp():
    """惰性导入 MCP SDK，缺失时给出可执行的修复提示。"""
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError("缺少 MCP 依赖，请执行：uv sync --extra multimodal") from exc
    return sse_client, ClientSession


def _pick_param_name(schema: dict[str, Any]) -> str:
    """从工具的 inputSchema 里选出图片地址对应的参数名。"""
    properties = (schema or {}).get("properties") or {}
    for candidate in _PARAM_CANDIDATES:
        if candidate in properties:
            return candidate
    if len(properties) == 1:
        # 只有一个参数时就用它——总比猜一个不存在的名字好
        return next(iter(properties))
    return "image_url"


def _extract_text(result: Any) -> str:
    """从 MCP 的 CallToolResult 中提取文本内容。"""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
        elif isinstance(block, dict) and block.get("text"):
            parts.append(str(block["text"]))
    return "\n".join(parts).strip()


class OcrMcpClient:
    """OCR 云服务的 MCP 客户端（SSE transport）。

    每次调用新建连接。SSE 是长连接，新建开销不小；演示规模（数百张图片）下
    这个开销可以接受，换来的是实现简单、不会因为长连接静默失效而卡住。
    批量场景应改为维护单例会话并在断开时重建。
    """

    def __init__(self, url: str, api_key: str, timeout: float = 20.0) -> None:
        self._url = url
        self._api_key = api_key
        self._timeout = timeout
        self._tool_name: str | None = None
        self._param_name: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self._url and self._api_key)

    @property
    def resolved_tool(self) -> str | None:
        """首次调用后可得：实际使用的工具名（排查签名问题用）。"""
        return self._tool_name

    async def recognize(self, image_url: str) -> str:
        """识别图片中的文字。失败时抛异常，由解析器转成 failed 产出。"""
        if not self.configured:
            raise RuntimeError("OCR 服务未配置，请设置 DASHSCOPE_API_KEY")
        try:
            return await asyncio.wait_for(self._invoke(image_url), timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"OCR 调用超时（>{self._timeout}s）") from exc

    async def _invoke(self, image_url: str) -> str:
        sse_client, ClientSession = _require_mcp()

        async with sse_client(self._url,
                              headers={"Authorization": f"Bearer {self._api_key}"}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tool_name, schema = await self._resolve_tool(session)
                arguments = {_pick_param_name(schema): image_url}
                result = await session.call_tool(tool_name, arguments)
                if getattr(result, "isError", False):
                    raise RuntimeError(f"OCR 工具返回错误：{_extract_text(result)[:200]}")
                return _extract_text(result)

    async def _resolve_tool(self, session: Any) -> tuple[str, dict[str, Any]]:
        """发现服务暴露的工具与入参 schema。结果缓存，后续调用不再查询。"""
        if self._tool_name and self._param_name:
            return self._tool_name, {"properties": {self._param_name: {}}}

        listing = await session.list_tools()
        tools = getattr(listing, "tools", None) or []
        if not tools:
            raise RuntimeError("OCR MCP 服务未暴露任何工具")

        tool = tools[0]
        name = getattr(tool, "name", None) or "ocr"
        schema = getattr(tool, "inputSchema", None) or {}
        self._tool_name = name
        self._param_name = _pick_param_name(schema)

        logger.info(
            "OCR 工具已发现 name={} 参数={} 可用工具数={}",
            name, self._param_name, len(tools))
        return name, schema

    async def close(self) -> None:
        """无长连接需要关闭，保留此接口以便将来改为会话复用时调用方无需改动。"""
        return None


class OcrMcpParser(ModalityParser):
    """基于 MCP 云端服务的图像 OCR 解析器。"""

    parser_name: ClassVar[str] = "ocr_mcp"
    asset_type: ClassVar[str] = ASSET_IMAGE

    def __init__(self, client: OcrMcpClient | None = None) -> None:
        self._client = client or default_client()

    @property
    def configured(self) -> bool:
        return self._client.configured

    async def parse(self, item: ModalityInput) -> ModalityOutput:
        if item.asset_type != ASSET_IMAGE:
            return self._failed(item, f"OCR 不支持资产类型 {item.asset_type}")
        if not item.origin_url:
            return self._failed(item, "缺少图片地址")
        if not self._client.configured:
            return self._failed(item, "OCR 服务未配置（DASHSCOPE_API_KEY 为空）")

        try:
            text = await self._client.recognize(item.origin_url)
        except Exception as exc:  # noqa: BLE001  失败必须放行
            logger.warning("OCR 解析失败 url={} err={}",
                           item.origin_url, mask_secret(f"{type(exc).__name__}: {exc}")[:160])
            return self._failed(item, f"{type(exc).__name__}: {exc}")

        # 用 strip 判断：服务可能返回纯空白，那等同于没识别出东西，
        # 标记为 success 会让上层以为拿到了有效文本
        if not text.strip():
            return self._failed(item, "OCR 返回空文本")
        return self._success(item, text)


def default_client() -> OcrMcpClient:
    """按配置构造客户端。"""
    settings = get_settings()
    return OcrMcpClient(
        url=settings.ocr_mcp_url,
        api_key=settings.dashscope_api_key,
        timeout=settings.ocr_timeout_seconds,
    )
