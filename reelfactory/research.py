"""Source-backed research: free news headlines or optional Tavily web search."""
from datetime import datetime, timezone
from html import unescape
import re
import xml.etree.ElementTree as ET

import requests

from .social import safe_url, setting


def search(root, query, source="news", days=7):
    query = query.strip()
    if not 2 <= len(query) <= 300:
        raise ValueError("Enter a search query between 2 and 300 characters.")
    if days not in {1, 7, 30} or source not in {"news", "web"}:
        raise ValueError("Choose news or web search and a 1, 7 or 30 day window.")
    try:
        if source == "web":
            key = setting(root, "TAVILY_API_KEY")
            if not key:
                raise ValueError("Add a Tavily API key in API settings for web search, or choose free news search.")
            response = requests.post("https://api.tavily.com/search",
                headers={"Authorization": "Bearer " + key},
                json={"query": query, "max_results": 8, "search_depth": "basic",
                      "time_range": {1: "day", 7: "week", 30: "month"}[days],
                      "include_answer": False, "include_published_date": True}, timeout=(10, 30), allow_redirects=False)
            response.raise_for_status()
            data = response.json()
            rows = [{"title": str(x.get("title", "")), "url": str(x.get("url", "")),
                     "summary": str(x.get("content", ""))[:1200],
                     "published": str(x.get("published_date") or "Date not provided"),
                     "publisher": "Web search"} for x in data.get("results", []) if isinstance(x, dict)]
        else:
            response = requests.get("https://news.google.com/rss/search",
                params={"q": query + f" when:{days}d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
                timeout=(10, 30), allow_redirects=False)
            response.raise_for_status()
            if len(response.content) > 2_000_000 or b"<!DOCTYPE" in response.content.upper() or b"<!ENTITY" in response.content.upper():
                raise ValueError("News response could not be read safely.")
            xml = ET.fromstring(response.content)
            rows = [{"title": x.findtext("title", ""), "url": x.findtext("link", ""),
                     "summary": unescape(re.sub(r"<[^>]+>", "", x.findtext("description", "")))[:1200],
                     "published": x.findtext("pubDate", "Date not provided"),
                     "publisher": x.findtext("source", "News")} for x in xml.findall("./channel/item")[:8]]
    except (requests.RequestException, ET.ParseError):
        raise ValueError("Research provider is unavailable. Try again later or change the search source.") from None
    except (TypeError, AttributeError):
        raise ValueError("Research provider returned an unexpected response.") from None
    seen, results = set(), []
    for row in rows:
        if safe_url(row["url"]) and row["url"] not in seen:
            results.append(row)
            seen.add(row["url"])
    return {"query": query, "source": source, "days": days, "results": results,
            "searched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
