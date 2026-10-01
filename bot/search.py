"""Websuche über DuckDuckGo (kostenlos, kein API-Key)."""
import time

try:
    from ddgs import DDGS  # neues Paket
except ImportError:  # pragma: no cover
    from duckduckgo_search import DDGS  # altes Paket


def search(query: str, max_results: int = 10) -> list[dict]:
    """Liste von {'title','href','body'}. Bei Rate-Limit einmal kurz warten."""
    for attempt in range(2):
        try:
            with DDGS() as d:
                res = list(d.text(query, region="de-de", max_results=max_results))
            time.sleep(2)
            return [{"title": r.get("title", ""), "href": r.get("href") or r.get("url", ""),
                     "body": r.get("body", "")} for r in res]
        except Exception:
            time.sleep(15 * (attempt + 1))
    return []
