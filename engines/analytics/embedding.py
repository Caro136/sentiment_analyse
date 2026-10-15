"""文本向量编码。

选云端接口而非本地模型（sentence-transformers）：本地模型要下载数百 MB 权重、
占用显存，而在这个数据量下云端调用的成本可以忽略，且**换供应商只需改配置**。

编码结果是聚类的输入——语义聚类靠它把"表达不同但意思相近"的内容归到一起，
这正是关键词匹配做不到的部分（同一条抱怨，有人说"发热严重"、有人说"烫手"、
有人说"暖手宝"，关键词匹配一个都抓不住，语义向量能）。
"""

from collections.abc import Sequence
from typing import Any

from engines.common.logging import logger, mask_secret
from engines.contracts.config import get_settings

# 空文本会被部分接口拒绝，用占位符保证「输入与输出一一对应」
_EMPTY_PLACEHOLDER = "空"

_client_cache: dict[tuple[str, str, str], Any] = {}

# 单次请求的文本条数上限。DashScope 的限制就是 10，
# 但写死一个数字迟早会在换供应商时失效——`_embed_batch` 会自动拆分重试。
DEFAULT_BATCH_SIZE = 10
MAX_SPLIT_DEPTH = 4


def _is_batch_too_large(exc: BaseException) -> bool:
    """判断异常是否为「单次请求条数超限」。"""
    text = str(exc).lower()
    return ("batch size" in text or "batch_size" in text
            or "too many" in text or "maximum" in text and "input" in text)


def _require_embeddings():
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("缺少 LLM 依赖，请执行：uv sync --extra llm") from exc
    return OpenAIEmbeddings


class Embedder:
    """文本向量编码器。"""

    def __init__(self, api_key: str, base_url: str, model: str,
                 batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._batch_size = max(1, batch_size)
        self._client: Any = None
        self.call_count = 0

    @classmethod
    def from_settings(cls) -> "Embedder":
        """按配置构造（未单独配置时回落到 DashScope）。"""
        settings = get_settings()
        api_key, base_url, model = settings.embedding_settings
        return cls(api_key, base_url, model,
                   batch_size=settings.embedding_batch_size)

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._model)

    @property
    def model_name(self) -> str:
        return self._model or "(未配置)"

    def _get_client(self):
        if self._client is None:
            if not self.configured:
                raise RuntimeError(
                    "向量编码未配置：请设置 EMBEDDING_API_KEY（或 DASHSCOPE_API_KEY）")
            key = (self._api_key, self._base_url, self._model)
            if key not in _client_cache:
                OpenAIEmbeddings = _require_embeddings()
                _client_cache[key] = OpenAIEmbeddings(
                    model=self._model,
                    api_key=self._api_key,
                    base_url=self._base_url,
                    # 部分厂商的兼容接口不支持 token 计数，开启会直接报错
                    check_embedding_ctx_length=False,
                )
            self._client = _client_cache[key]
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量编码，返回与输入等长的向量列表。

        分批请求：单次请求条数过多会被接口拒绝，也会让失败时的重试代价变大。
        """
        if not texts:
            return []

        prepared = [(t or "").strip() or _EMPTY_PLACEHOLDER for t in texts]
        client = self._get_client()

        vectors: list[list[float]] = []
        for start in range(0, len(prepared), self._batch_size):
            batch = prepared[start:start + self._batch_size]
            vectors.extend(await self._embed_batch(client, batch))

        if len(vectors) != len(prepared):
            raise RuntimeError(
                f"编码结果数量不匹配：输入 {len(prepared)} 条，返回 {len(vectors)} 条")
        return vectors

    async def _embed_batch(self, client: Any, batch: list[str],
                           depth: int = 0) -> list[list[float]]:
        """编码一批，遇到「条数超限」自动二分重试。

        写死批大小不可靠：不同供应商、甚至同一家的不同模型，限制都不一样
        （实测 DashScope 的 text-embedding-v4 上限是 10 条，
        超了直接 400，报错信息是 "batch size is invalid"）。
        与其让使用者去猜这个数字，不如让编码器自己适配。
        """
        try:
            result = await client.aembed_documents(batch)
            self.call_count += 1
            return result
        except Exception as exc:  # noqa: BLE001
            if (_is_batch_too_large(exc) and len(batch) > 1
                    and depth < MAX_SPLIT_DEPTH):
                half = len(batch) // 2
                logger.info("编码批大小 {} 超限，拆分为 {} + {} 重试",
                            len(batch), half, len(batch) - half)
                head = await self._embed_batch(client, batch[:half], depth + 1)
                tail = await self._embed_batch(client, batch[half:], depth + 1)
                return head + tail
            logger.warning("向量编码失败 batch={} err={}", len(batch),
                           mask_secret(f"{type(exc).__name__}: {exc}")[:200])
            raise

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0] if vectors else []

    def __repr__(self) -> str:
        state = "configured" if self.configured else "unconfigured"
        return f"<Embedder model={self.model_name} {state}>"
