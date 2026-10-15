"""统一的大模型客户端。

**按角色配置**是本项目的一个基本设计：打标（analytics）调用量最大、追求成本，
报告生成追求成稿质量，主持人追求稳定的判断——用同一个模型是浪费，
且无法在某一角色效果不佳时单独调优。

职责：
  - 从配置读取角色的模型、地址、密钥、采样参数
  - 提供两类门面：`generate_text`（自由文本）与 `generate_object`（结构化输出）
  - 统一重试与脱敏日志

不做：Prompt 的组织与业务语义（那是各引擎的事）。

依赖：`langchain-openai`（OpenAI 兼容协议）。未安装时本模块仍可导入，
实例化时才报错并给出安装提示——避免让可选依赖拖垮整个工程的导入链。
"""

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any, Literal, TypeVar, get_args, get_origin

from pydantic import BaseModel, ValidationError

from engines.common.logging import logger, mask_secret
from engines.common.runtime.call_retry import DEFAULT_RETRY, RetryConfig, with_retry
# 并发工具现由 common.runtime.concurrency 提供（多模态解析等模块也要用）。
# 这里重新导出，保持既有调用方的导入路径不变。
from engines.common.runtime.concurrency import gather_limited
from engines.contracts.config import LLMRoleSettings, get_settings

TModel = TypeVar("TModel", bound=BaseModel)

# 各供应商的差异处理。当前只做温度上限约束——不同厂商对 temperature 的
# 合法区间不同（0~1 与 0~2 都有），传超范围的值会被直接拒绝。
PROVIDER_TEMPERATURE_CEILING: dict[str, float] = {
    "moonshot": 1.0,
    "deepseek": 2.0,
    "dashscope": 2.0,
    "openai": 2.0,
    "openai_compatible": 2.0,
}


def detect_provider(base_url: str, model_name: str) -> str:
    """按接入地址与模型名推断供应商，用于选择差异化的调用参数。"""
    url = (base_url or "").lower()
    model = (model_name or "").lower()
    if "moonshot" in url or "kimi" in model:
        return "moonshot"
    if "deepseek" in url or "deepseek" in model:
        return "deepseek"
    if "dashscope" in url or "qwen" in model or "aliyuncs" in url:
        return "dashscope"
    if "openai.com" in url:
        return "openai"
    return "openai_compatible"


# 降级路径的指令。逐字段说明而非直接贴 JSON Schema——
# 贴 schema 时模型经常把它当成"要输出的内容"原样复述一遍，
# 于是输出里出现 `{"description": ..., "properties": ...}` 这种东西。
_JSON_INSTRUCTION = (
    "\n\n请只输出一个 JSON 对象，不要任何解释文字，不要用代码块包裹。\n"
    "字段要求：\n{fields}"
)


def _type_label(annotation: Any) -> str:
    """把类型注解转成人话，用于生成字段说明。"""
    origin = get_origin(annotation)
    if origin is Literal:
        return " 或 ".join(f'"{arg}"' for arg in get_args(annotation))
    mapping = {str: "字符串", float: "小数", int: "整数", bool: "true/false"}
    return mapping.get(annotation, str(annotation).replace("typing.", ""))


def _describe_fields(schema: type[BaseModel]) -> str:
    """生成字段说明，供降级路径的提示词使用。"""
    lines = []
    for name, field in schema.model_fields.items():
        default = "" if field.is_required() else f"，可不填"
        description = (field.description or "").strip()
        lines.append(f"- {name}（{_type_label(field.annotation)}{default}）：{description}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象。

    逐段扫描而不是"取第一个 { 到最后一个 }"：后者在模型复述了 schema
    或输出多个对象时会拼出一段非法 JSON。这里用 raw_decode 依次解析每个
    可能的位置，**取最后一个成功的对象**——模型常把说明放在前、结果放在后。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    index = 0
    while True:
        start = cleaned.find("{", index)
        if start == -1:
            break
        try:
            obj, consumed = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(obj, dict):
            candidates.append(obj)
        index = start + max(consumed, 1)

    if not candidates:
        raise ValueError("模型输出中找不到可解析的 JSON 对象")
    return candidates[-1]


def _is_structured_output_unsupported(exc: BaseException) -> bool:
    """判断异常是否为「该模型不支持结构化输出」。

    典型情形：DeepSeek 等推理模型处于 thinking 模式时拒绝 tool_choice，
    返回 400 "Thinking mode does not support this tool_choice"。
    这不是我们调用姿势的问题，而是模型能力边界，应当降级而非重试。
    """
    text = str(exc).lower()
    return ("tool_choice" in text
            or "thinking mode" in text
            or ("does not support" in text and "tool" in text))


def _require_chat_model():
    """惰性导入 LangChain，缺失时给出可执行的修复提示。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise RuntimeError(
            "缺少 LLM 依赖，请执行：uv sync --extra llm"
        ) from exc
    return ChatOpenAI


class LLMNotConfiguredError(RuntimeError):
    """角色未配置模型。抛出明确异常，避免出现难以定位的调用失败。"""


class LLMClient:
    """按角色配置的大模型客户端。"""

    def __init__(self, role: str, config: LLMRoleSettings, *,
                 retry: RetryConfig | None = None):
        self.role = role
        self.provider = detect_provider(config.base_url, config.model_name)
        self._config = config
        self._retry = retry or DEFAULT_RETRY
        self._chat_model: Any = None
        self.call_count = 0
        # 该模型是否已知不支持结构化输出。首次失败后记住，
        # 后续直接走降级路径——否则每次调用都要先撞一次注定失败的 tool_choice，
        # 白等一个来回（实测这一步让章节生成慢了近一倍）。
        self._structured_unsupported = False

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------

    @classmethod
    def from_role(cls, role_key: str, **kwargs) -> "LLMClient":
        """按角色标识创建客户端（配置取自 .env，各角色独立）。"""
        settings = get_settings()
        return cls(role_key, settings.llm_for_role(role_key), **kwargs)

    @property
    def configured(self) -> bool:
        return self._config.configured

    @property
    def model_name(self) -> str:
        return self._config.model_name or "(未配置)"

    def _model(self):
        """惰性构建底层模型对象。"""
        if self._chat_model is None:
            if not self.configured:
                raise LLMNotConfiguredError(
                    f"角色 {self.role!r} 未配置模型：请在 .env 中设置 "
                    f"{self.role.upper()}_API_KEY 与 {self.role.upper()}_MODEL_NAME"
                )
            ChatOpenAI = _require_chat_model()
            kwargs: dict[str, Any] = {
                "model": self._config.model_name,
                "api_key": self._config.api_key,
                "timeout": self._config.timeout_seconds,
            }
            if self._config.base_url:
                kwargs["base_url"] = self._config.base_url
            if self._config.max_tokens:
                kwargs["max_tokens"] = self._config.max_tokens
            if self._config.temperature is not None:
                ceiling = PROVIDER_TEMPERATURE_CEILING.get(self.provider, 2.0)
                if self._config.temperature > ceiling:
                    logger.warning(
                        "角色 {} 的 temperature={} 超过 {} 的上限 {}，已收敛",
                        self.role, self._config.temperature, self.provider, ceiling)
                    kwargs["temperature"] = ceiling
                else:
                    kwargs["temperature"] = self._config.temperature
            self._chat_model = ChatOpenAI(**kwargs)
        return self._chat_model

    # ------------------------------------------------------------------
    # 门面方法
    # ------------------------------------------------------------------

    @with_retry(operation="llm.generate_text")
    async def generate_text(self, prompt: str, *, system: str | None = None,
                            temperature: float | None = None) -> str:
        """生成自由文本。"""
        messages = self._build_messages(prompt, system)
        started = time.monotonic()
        model = self._model()
        if temperature is not None:
            model = model.bind(temperature=temperature)

        response = await model.ainvoke(messages)
        self.call_count += 1
        self._log_call("text", started, self._content_of(response))
        return self._content_of(response)

    @with_retry(operation="llm.generate_object")
    async def generate_object(self, prompt: str, schema: type[TModel], *,
                              system: str | None = None) -> TModel:
        """生成结构化对象。

        优先用 function_calling 约束输出——模型直接返回符合 schema 的对象，
        调用方不必解析与容错。但**并非所有模型都支持**：
        推理类模型（如 DeepSeek 的 thinking 模式）会直接拒绝 tool_choice，
        报 400「Thinking mode does not support this tool_choice」。
        这种情况自动降级为「要求模型输出 JSON 文本再解析」，
        而不是让整个打标流程挂掉。
        """
        messages = self._build_messages(prompt, system)
        started = time.monotonic()

        if self._structured_unsupported:
            result = await self._object_via_text(prompt, schema, system)
        else:
            try:
                structured = self._model().with_structured_output(
                    schema, method="function_calling")
                result = await structured.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001
                if not _is_structured_output_unsupported(exc):
                    raise
                self._structured_unsupported = True
                logger.info("角色 {} 的模型 {} 不支持结构化输出，"
                            "后续调用直接走 JSON 文本解析", self.role, self.model_name)
                result = await self._object_via_text(prompt, schema, system)

        self.call_count += 1
        self._log_call("object", started, str(result)[:200])
        return result

    async def _object_via_text(self, prompt: str, schema: type[TModel],
                               system: str | None) -> TModel:
        """降级路径：让模型直接输出 JSON，再解析为对象。

        解析失败时抛出 ValueError（附原始输出片段），由重试器决定是否重试——
        模型输出有随机性，重试一次往往就能拿到合法 JSON。
        """
        instruction = _JSON_INSTRUCTION.format(fields=_describe_fields(schema))
        text = await self.generate_text(prompt + instruction, system=system)
        try:
            return schema.model_validate(_extract_json_object(text))
        except (ValueError, ValidationError) as exc:
            raise ValueError(
                f"模型输出无法解析为 {schema.__name__}："
                f"{mask_secret(str(exc))[:160]} | 原文：{text[:120]}"
            ) from exc

    async def stream_text(self, prompt: str, *, system: str | None = None) -> AsyncIterator[str]:
        """流式生成文本片段。

        不加重试装饰器：流式响应在中途失败时，重试会导致已推送片段重复。
        调用方若需要重试，应在收集完整文本后自行判断。
        """
        messages = self._build_messages(prompt, system)
        started = time.monotonic()
        chunks = 0
        try:
            async for chunk in self._model().astream(messages):
                text = self._content_of(chunk)
                if text:
                    chunks += 1
                    yield text
        finally:
            self.call_count += 1
            self._log_call("stream", started, f"{chunks} 个片段")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(prompt: str, system: str | None) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _content_of(response: Any) -> str:
        """兼容不同版本的返回结构：字符串、带 content 的对象、内容块列表。"""
        content = getattr(response, "content", response)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                else:
                    text = getattr(block, "text", None)
                    if text:
                        parts.append(str(text))
            return "".join(parts)
        return str(content)

    def _log_call(self, kind: str, started: float, preview: str) -> None:
        elapsed = time.monotonic() - started
        logger.info(
            "LLM 调用 role={} kind={} model={} 耗时={:.2f}s 输出={} 字 | {}",
            self.role, kind, self.model_name, elapsed, len(preview),
            mask_secret(preview[:60]).replace("\n", " "),
        )

    def __repr__(self) -> str:
        state = "configured" if self.configured else "unconfigured"
        return f"<LLMClient role={self.role} model={self.model_name} {state}>"


# ---------------------------------------------------------------------------
# 便捷入口
# ---------------------------------------------------------------------------

def client_for(role_key: str) -> LLMClient:
    """按角色取客户端的快捷函数（等价于 LLMClient.from_role）。"""
    return LLMClient.from_role(role_key)


__all__ = [
    "LLMClient",
    "LLMNotConfiguredError",
    "client_for",
    "detect_provider",
    "gather_limited",       # 重新导出，见文件顶部说明
]
