#!/usr/bin/env python3
"""Search the Sideria Bridge meme library.

The bridge owns meme storage and delivery.  Hermes only searches its local
HTTP API and returns stable meme ids; the bridge later converts a selected
``&&meme:ID&&`` marker in the assistant response into the actual QQ image.
"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from tools.registry import registry, tool_error, tool_result


DEFAULT_MEME_BRIDGE_URL = "http://127.0.0.1:29998"
_REQUEST_TIMEOUT_SECONDS = 3.0


def _load_config() -> dict:
    try:
        from hermes_cli.config import load_config_readonly

        section = load_config_readonly().get("meme_search")
        return section if isinstance(section, dict) else {}
    except Exception:
        return {}


def _base_url() -> str:
    configured = str(_load_config().get("base_url") or DEFAULT_MEME_BRIDGE_URL).strip()
    return configured.rstrip("/")


def _is_loopback_http_url(value: str) -> bool:
    """Keep this bridge-specific tool confined to the local machine."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }


def _request_json(path: str, params: dict | None = None) -> dict:
    base_url = _base_url()
    if not _is_loopback_http_url(base_url):
        raise ValueError("meme_search.base_url must point to a loopback host")
    query = f"?{urlencode(params)}" if params else ""
    request = Request(
        f"{base_url}{path}{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("meme bridge returned a non-object response")
    return payload


def check_meme_search_requirements() -> bool:
    try:
        _request_json("/api/memes/search", {"q": "", "limit": 1})
        return True
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return False


def search_memes_tool(query: str, limit: int = 5) -> str:
    query = str(query or "").strip()
    try:
        limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        return tool_error("limit must be an integer between 1 and 10")

    try:
        payload = _request_json(
            "/api/memes/search",
            {"q": query, "limit": limit},
        )
    except HTTPError as exc:
        return tool_error(f"Meme bridge returned HTTP {exc.code}")
    except (URLError, OSError) as exc:
        return tool_error(f"Meme bridge is unavailable: {exc}")
    except (ValueError, json.JSONDecodeError) as exc:
        return tool_error(f"Invalid meme bridge response: {exc}")

    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return tool_error("Meme bridge response is missing a results list")

    results = []
    for item in raw_results[:limit]:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        results.append(
            {
                key: item[key]
                for key in ("id", "tag", "category", "description", "keywords", "score")
                if key in item
            }
        )

    return tool_result(
        query=query,
        results=results,
        usage=(
            "To send one result, put &&meme:ID&& on its own line at the end of "
            "your final response, replacing ID with the chosen result id. Use at "
            "most one and omit the marker when no result fits naturally."
        ),
    )


SEARCH_MEMES_SCHEMA = {
    "name": "search_memes",
    "description": (
        "Search the local Sideria Bridge meme library by emotion, situation, tag, "
        "category, or keyword. Use this when a meme would genuinely improve a QQ "
        "reply. The result contains stable ids; select at most one by following the "
        "returned marker instructions. Do not use a meme in every reply."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Desired emotion, situation, tag, or keyword.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 5,
                "description": "Maximum candidates to return.",
            },
        },
        "required": ["query"],
    },
}


registry.register(
    name="search_memes",
    toolset="meme_search",
    schema=SEARCH_MEMES_SCHEMA,
    handler=lambda args, **kw: search_memes_tool(
        query=args.get("query", ""),
        limit=args.get("limit", 5),
    ),
    check_fn=check_meme_search_requirements,
    emoji="🎭",
)
