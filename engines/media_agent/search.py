"""公域搜索适配器。

三家供应商的定位不同（选型依据见设计规格 2.2）：

| 供应商 | 强项 | 弱项 |
|--------|------|------|
| Tavily | 海外英文资源、深度阅读 | 中文生态穿透力差、有数据出海顾虑 |
| Bocha | 国内中文网络、自带重排 | 英文学术冷门站点较弱 |
| Anspire | 企业级合规、多渠道深搜 | 偏商业定制 |

统一成同一套接口后，上层（章节检索）不感知供应商差异。

**失败降级**：配置指定的供应商不可用时，自动尝试其他已配置的。
实测就遇到过——配置写的是 bocha，但密钥返回 403，
若不降级，整条公域链路会直接空转。
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from engines.common.logging import logger, mask_secret
from engines.contracts.config import get_settings

DEFAULT_TIMEOUT = 25.0
DEFAULT_COUNT = 5


@dataclass(frozen=True)
class SearchResult:
    """归一化后的搜索结果。"""

    title: str
    url: str
    content: str
    published_at: str = ""
    source: str = ""          # 供应商名，用于诊断与展示

    @property
    def summary(self) -> str:
        text = f"{self.title}。{self.content}".strip("。")
        return text[:200] + ("…" if len(text) > 200 else "")


class SearchProvider(ABC):
    """搜索供应商的统一接口。"""

    name: ClassVar[str]

    @property
    @abstractmethod
    def configured(self) -> bool:
        """是否具备调用条件（有密钥）。"""

    @abstractmethod
    async def search(self, query: str, *, count: int = DEFAULT_COUNT,
                     time_range: str = "") -> list[SearchResult]:
        """执行检索，失败时抛异常由上层降级。"""


class TavilyProvider(SearchProvider):
    """Tavily：全球化搜索，深度阅读能力强。"""

    name: ClassVar[str] = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def search(self, query: str, *, count: int = DEFAULT_COUNT,
                     time_range: str = "") -> list[SearchResult]:
        payload: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "max_results": count,
            "search_depth": "basic",
        }
        if time_range:
            payload["time_range"] = time_range

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(self.ENDPOINT, json=payload)
            response.raise_for_status()
            data = response.json()

        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                content=str(item.get("content", "")),
                published_at=str(item.get("published_date", "") or ""),
                source=self.name,
            )
            for item in (data.get("results") or [])
        ]


class BochaProvider(SearchProvider):
    """Bocha（博查）：国内中文知识搜索，自带二次语义重排。"""

    name: ClassVar[str] = "bocha"

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._base_url)

    async def search(self, query: str, *, count: int = DEFAULT_COUNT,
                     time_range: str = "") -> list[SearchResult]:
        payload: dict[str, Any] = {"query": query, "count": count}
        # Bocha 用 oneDay/oneWeek/oneMonth/oneYear 表达时间范围
        if time_range:
            payload["freshness"] = time_range

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json"},
                json=payload)
            response.raise_for_status()
            data = response.json()

        pages = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        return [
            SearchResult(
                title=str(page.get("name", "")),
                url=str(page.get("url", "")),
                content=str(page.get("snippet") or page.get("summary") or ""),
                published_at=str(page.get("datePublished", "") or ""),
                source=self.name,
            )
            for page in pages
        ]


class AnspireProvider(SearchProvider):
    """Anspire：企业级合规搜索。"""

    name: ClassVar[str] = "anspire"

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    @property
    def configured(self) -> bool:
        return bool(self._api_key and self._base_url)

    async def search(self, query: str, *, count: int = DEFAULT_COUNT,
                     time_range: str = "") -> list[SearchResult]:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            response = await client.post(
                self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json"},
                json={"query": query, "top_k": count})
            response.raise_for_status()
            data = response.json()

        items = data.get("data") or data.get("results") or []
        return [
            SearchResult(
                title=str(item.get("title", "")),
                url=str(item.get("url", "")),
                content=str(item.get("content") or item.get("snippet") or ""),
                source=self.name,
            )
            for item in items
        ]


def build_providers() -> dict[str, SearchProvider]:
    """按配置构造全部供应商（不判断可用性）。"""
    settings = get_settings()
    return {
        "tavily": TavilyProvider(settings.tavily_api_key),
        "bocha": BochaProvider(settings.bocha_base_url, settings.bocha_api_key),
        "anspire": AnspireProvider(settings.anspire_base_url, settings.anspire_api_key),
    }


class SearchService:
    """搜索服务：按配置选供应商，失败时降级到其他可用的。"""

    def __init__(self, preferred: str | None = None,
                 providers: dict[str, SearchProvider] | None = None) -> None:
        settings = get_settings()
        self._preferred = preferred or settings.search_tool_type or "tavily"
        self._providers = providers if providers is not None else build_providers()
        self.last_provider = ""
        # 本次会话中已失败的供应商：一次研究要发十几个检索请求，
        # 不记住失败的话，每个请求都要先撞一次注定失败的供应商白等一个来回
        self._failed: set[str] = set()

    def _order(self) -> list[SearchProvider]:
        """供应商尝试顺序：配置的优先，其余已配置的依次兜底。

        已失败的跳过——但只跳过本次会话内，不跨研究任务持久化：
        失败可能是限流之类的临时问题，下次研究应当重新尝试。
        """
        ordered: list[SearchProvider] = []
        first = self._providers.get(self._preferred)
        if first is not None and first.configured and first.name not in self._failed:
            ordered.append(first)
        for name, provider in self._providers.items():
            if (name != self._preferred and provider.configured
                    and name not in self._failed):
                ordered.append(provider)
        return ordered

    @property
    def available(self) -> list[str]:
        return [p.name for p in self._order()]

    async def search(self, query: str, *, count: int = DEFAULT_COUNT,
                     time_range: str = "") -> list[SearchResult]:
        """执行检索。全部供应商都失败时返回空列表（不抛异常）。

        返回空而不是抛：搜索是公域研究的**唯一**数据来源，但它失败时
        应当由上层决定「用什么话术说明缺材料」，而不是让整条链路中断。
        """
        if not query.strip():
            return []

        providers = self._order()
        if not providers:
            logger.warning("没有可用的搜索供应商（检查 SEARCH_TOOL_TYPE 与各家密钥）")
            return []

        for provider in providers:
            try:
                results = await provider.search(query, count=count, time_range=time_range)
                self.last_provider = provider.name
                logger.info("搜索完成 provider={} query={!r} 命中 {} 条",
                            provider.name, query[:40], len(results))
                return results
            except Exception as exc:  # noqa: BLE001
                self._failed.add(provider.name)
                logger.warning("搜索失败 provider={} err={}，本次会话内不再尝试该供应商",
                               provider.name, mask_secret(
                                   f"{type(exc).__name__}: {exc}")[:150])
                await asyncio.sleep(0)

        logger.error("全部搜索供应商均失败：{}", [p.name for p in providers])
        return []

    async def search_many(self, queries: list[str], *, count: int = DEFAULT_COUNT,
                          time_range: str = "") -> list[SearchResult]:
        """按多个检索词逐个检索并去重（按 URL）。

        串行而非并发：搜索 API 通常有 QPS 限制，且这里每次只发 2~3 个词，
        并发的收益抵不上触发限流的风险。
        """
        seen: set[str] = set()
        merged: list[SearchResult] = []
        for query in queries:
            for result in await self.search(query, count=count, time_range=time_range):
                key = result.url or result.title
                if key in seen:
                    continue
                seen.add(key)
                merged.append(result)
        return merged
