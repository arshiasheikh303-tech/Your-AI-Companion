from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class DuckDuckGoProvider(SearchProvider):
    """Free, no API key. Uses the duckduckgo-search package if installed, else the HTML endpoint."""

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            from duckduckgo_search import DDGS  # type: ignore

            import asyncio

            def _run():
                with DDGS() as d:
                    return list(d.text(query, max_results=max_results))

            rows = await asyncio.to_thread(_run)
            return [SearchResult(r.get("title", ""), r.get("href", ""), r.get("body", "")) for r in rows]
        except ImportError:
            pass
        import re
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as c:
            resp = await c.post("https://html.duckduckgo.com/html/", data={"q": query})
            resp.raise_for_status()
        out = []
        for m in re.finditer(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                             resp.text, re.S):
            strip = lambda x: re.sub(r"<[^>]+>", "", x).strip()
            out.append(SearchResult(strip(m.group(2)), m.group(1), strip(m.group(3))))
            if len(out) >= max_results:
                break
        return out


def get_search_provider() -> SearchProvider | None:
    from app.config import get_settings

    name = get_settings().search_provider.lower()
    if name in ("", "none", "off"):
        return None
    if name == "duckduckgo":
        return DuckDuckGoProvider()
    log.warning("unknown SEARCH_PROVIDER %s; search disabled", name)
    return None
