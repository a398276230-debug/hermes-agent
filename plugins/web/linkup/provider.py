"""Linkup (https://www.linkup.so) web search + page fetch — Bearer-auth REST.

Config: ``web.search_backend`` / ``web.extract_backend`` / ``web.backend: "linkup"``.
Env: ``LINKUP_API_KEY`` (https://app.linkup.so/, required), ``LINKUP_BASE_URL``
(default ``https://api.linkup.so/v1``). Search posts the query to ``/search``;
extract issues one ``/fetch`` request per URL and returns the page markdown.

Search tuning (config first, then env, then default):

============================  ============================  ==========================
``web.linkup.<key>``          env fallback                  default
============================  ============================  ==========================
``depth``                     ``LINKUP_DEPTH``              ``standard``
``include_domains``           ``LINKUP_INCLUDE_DOMAINS``    unset
``exclude_domains``           ``LINKUP_EXCLUDE_DOMAINS``    unset
============================  ============================  ==========================

A ``site:example.com`` / ``-site:example.com`` token in the query is lifted into the domain
lists and stripped from ``q`` — Linkup has no native site operator.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

import httpx

from plugins.web._common import (
    SEARCH_LIMIT_CAP, BaseWebSearchProvider, document, extract_fail, http_status_detail,
    page_error, provider_env, run_extract, run_search, search_fail, search_ok, setup_schema, web_hit,
)

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.linkup.so/v1"
_DEFAULT_DEPTH = "standard"  # single agentic pass; flash/fast trade coverage for latency
_VALID_DEPTHS = ("flash", "fast", "standard", "deep")
_TIMEOUT_S = 60
_MISSING_KEY = (
    "LINKUP_API_KEY is not set. Get a key at https://app.linkup.so/ and add it to ~/.hermes/.env "
    "(or select Linkup in `hermes tools`)."
)

# ``site:example.com`` / ``-site:example.com`` — matched per whitespace-delimited token so
# substrings (``docs/site:foo``) never parse as operators.
_SITE_OPERATOR_RE = re.compile(r"(-?)site:(\S+)", re.IGNORECASE)
_DOMAIN_TRIM = ".,;"


def _base_url() -> str:
    return (provider_env("LINKUP_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _web_linkup_config() -> Dict[str, Any]:
    """The ``web.linkup`` mapping from config.yaml (``{}`` when absent or not a mapping).
    Imported lazily so tests can patch ``hermes_cli.config.load_config``."""
    try:
        from hermes_cli.config import load_config
        section = load_config().get("web") or {}
    except Exception as exc:  # noqa: BLE001 — config is optional; env/defaults still apply
        logger.debug("Linkup: could not read web config: %s", exc)
        return {}
    linkup = section.get("linkup")
    return linkup if isinstance(linkup, dict) else {}


def _unset(value: Any) -> bool:
    """Config treats absent/null/blank as "not configured" so the env fallback still applies."""
    return value is None or (isinstance(value, str) and not value.strip())


def _normalize_domains(value: Any) -> List[str]:
    """A comma-separated string, a list/tuple/set, or None → ordered, unique, normalized domains."""
    if isinstance(value, str):
        parts: List[Any] = [value]
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        return []
    domains: List[str] = []
    for part in parts:
        for candidate in str(part).split(","):
            domain = candidate.strip().strip(_DOMAIN_TRIM).lower()
            if domain and domain not in domains:
                domains.append(domain)
    return domains


def _merge_unique(*groups: List[str]) -> List[str]:
    merged: List[str] = []
    for group in groups:
        for item in group:
            if item not in merged:
                merged.append(item)
    return merged


def _resolve_depth(cfg: Dict[str, Any]) -> str:
    """config → env → default, validated; an out-of-set value logs and falls back to standard."""
    raw = cfg.get("depth")
    if _unset(raw):
        raw = provider_env("LINKUP_DEPTH")
    depth = str(raw or "").strip().lower()
    if depth in _VALID_DEPTHS:
        return depth
    if depth:
        logger.debug("Linkup: unknown depth %r — falling back to %s", raw, _DEFAULT_DEPTH)
    return _DEFAULT_DEPTH


def _resolve_domains(cfg: Dict[str, Any], key: str, env_name: str) -> List[str]:
    value = cfg.get(key)
    if _unset(value):
        value = provider_env(env_name)
    return _normalize_domains(value)


def _resolve_settings() -> Dict[str, Any]:
    """One config load per search: depth plus the configured (not query-derived) domain lists."""
    cfg = _web_linkup_config()
    return {
        "depth": _resolve_depth(cfg),
        "include_domains": _resolve_domains(cfg, "include_domains", "LINKUP_INCLUDE_DOMAINS"),
        "exclude_domains": _resolve_domains(cfg, "exclude_domains", "LINKUP_EXCLUDE_DOMAINS"),
    }


def _parse_query(query: str) -> tuple[str, List[str], List[str]]:
    """Lift ``site:``/``-site:`` operators out of *query*.

    Returns ``(clean_query, include_domains, exclude_domains)``. Operator tokens are dropped so
    Linkup sees the natural-language remainder; a query made *only* of operators is kept verbatim
    (the domain lists still carry the filter).
    """
    include: List[str] = []
    exclude: List[str] = []
    kept: List[str] = []
    for token in query.split():
        match = _SITE_OPERATOR_RE.fullmatch(token)
        domain = match.group(2).strip(_DOMAIN_TRIM).lower() if match else ""
        if not domain:
            kept.append(token)
            continue
        target = exclude if match.group(1) else include
        if domain not in target:
            target.append(domain)
    cleaned = " ".join(kept).strip()
    return (cleaned or query.strip(), include, exclude)


def _post(endpoint: str, payload: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """POST to Linkup. Non-2xx raises ``ValueError`` carrying the response body verbatim so
    Linkup's own quota/auth text reaches the model."""
    url = f"{_base_url()}/{endpoint}"
    logger.info("Linkup %s request to %s", endpoint, url)
    response = httpx.post(
        url, json=payload, timeout=_TIMEOUT_S,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    if response.status_code >= 400:
        raise ValueError(http_status_detail(response))
    return response.json()


def _normalize_search_results(response: Dict[str, Any]) -> Dict[str, Any]:
    """Linkup search rows are ``{name, url, content}`` — ``name`` is the title."""
    return search_ok([
        web_hit(str(r.get("url") or ""), str(r.get("name") or ""), str(r.get("content") or ""), i + 1)
        for i, r in enumerate(response.get("results") or [])
    ])


def _title_from_markdown(markdown: str) -> str:
    """``/fetch`` returns no title; use the leading markdown heading when the page has one."""
    for line in markdown.splitlines():
        text = line.strip()
        if not text:
            continue
        return text.lstrip("#").strip() if text.startswith("#") else ""
    return ""


def _fetch_document(url: str, api_key: str) -> Dict[str, Any]:
    """One ``/fetch`` call → one document; a per-URL failure stays that URL's ``error`` entry."""
    try:
        raw = _post("fetch", {"url": url}, api_key)
    except Exception as exc:  # noqa: BLE001 — one bad page must not sink the batch
        logger.warning("Linkup fetch failed for %s: %s", url, exc)
        return page_error(url, f"Linkup fetch failed: {exc}")
    markdown = str(raw.get("markdown") or "")
    return document(url, _title_from_markdown(markdown), markdown)


class LinkupWebSearchProvider(BaseWebSearchProvider):
    """Linkup search + extract provider (keyed only — no anonymous tier)."""

    NAME = "linkup"
    DISPLAY_NAME = "Linkup"
    KEY_ENV = "LINKUP_API_KEY"
    EXTRACT = True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        def _body() -> Dict[str, Any]:
            api_key = provider_env("LINKUP_API_KEY")
            if not api_key:
                return search_fail(_MISSING_KEY)
            settings = _resolve_settings()
            clean_query, site_include, site_exclude = _parse_query(query)
            include = _merge_unique(site_include, settings["include_domains"])
            exclude = _merge_unique(site_exclude, settings["exclude_domains"])
            logger.info(
                "Linkup search: '%s' (limit=%d, depth=%s, include=%d, exclude=%d)",
                clean_query, limit, settings["depth"], len(include), len(exclude),
            )
            payload = {
                "q": clean_query,
                "depth": settings["depth"],
                "outputType": "searchResults",
                "maxResults": max(1, min(int(limit), SEARCH_LIMIT_CAP)),
            }
            if include:
                payload["includeDomains"] = include
            if exclude:
                payload["excludeDomains"] = exclude
            return _normalize_search_results(_post("search", payload, api_key))

        return run_search("Linkup", logger, _body)

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        def _body() -> List[Dict[str, Any]]:
            api_key = provider_env("LINKUP_API_KEY")
            if not api_key:
                return extract_fail(urls, _MISSING_KEY)
            logger.info("Linkup extract: %d URL(s)", len(urls))
            return [_fetch_document(url, api_key) for url in urls]

        return run_extract("Linkup", logger, urls, _body)

    def get_setup_schema(self) -> Dict[str, Any]:
        return setup_schema(
            "Linkup", "paid", "Web search + page fetch via Linkup's API. Requires LINKUP_API_KEY.",
            "LINKUP_API_KEY", "Linkup API key", "https://app.linkup.so/",
        )
