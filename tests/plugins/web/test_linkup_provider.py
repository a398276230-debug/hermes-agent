"""Tests for the bundled Linkup web search + extract provider plugin.

Coverage:
  _normalize_search_results / _title_from_markdown — response normalization.
  _parse_query / _normalize_domains — site: operator and domain-list parsing.
  _post — endpoint, Bearer auth, LINKUP_BASE_URL override, verbatim error bodies.
  search() / extract() — envelopes, depth + domain tuning, missing key, network failure,
  per-URL failures.
  is_available() + plugin discovery + web_search / web_extract dispatch.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import httpx
import pytest


def _response(payload, *, status_code: int = 200, text: str | None = None) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = payload
    mock.text = json.dumps(payload) if text is None else text
    return mock


@pytest.fixture(autouse=True)
def _clean_linkup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with no Linkup credentials/tuning and a cold search memo."""
    for key in (
        "LINKUP_API_KEY", "LINKUP_BASE_URL", "LINKUP_DEPTH",
        "LINKUP_INCLUDE_DOMAINS", "LINKUP_EXCLUDE_DOMAINS",
    ):
        monkeypatch.delenv(key, raising=False)
    from tools.web_result_cache import search_memo

    search_memo.clear()
    yield
    search_memo.clear()


@pytest.fixture
def linkup_config(monkeypatch: pytest.MonkeyPatch):
    """Install a ``web.linkup`` config mapping into the plugin's lazily imported loader."""
    import hermes_cli.config as config_mod

    def _install(section: dict) -> None:
        monkeypatch.setattr(config_mod, "load_config", lambda: {"web": {"linkup": section}})

    return _install


def _search_payload(monkeypatch: pytest.MonkeyPatch, query: str = "q", limit: int = 5) -> dict:
    """Run one keyed search and return the JSON body posted to /search."""
    monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
    with patch("plugins.web.linkup.provider.httpx.post", return_value=_response({"results": []})) as mock_post:
        from plugins.web.linkup.provider import LinkupWebSearchProvider

        LinkupWebSearchProvider().search(query, limit=limit)
    return mock_post.call_args.kwargs["json"]


# ─── normalization ───────────────────────────────────────────────────────────


class TestNormalizeSearchResults:
    def test_rows_map_name_to_title(self):
        from plugins.web.linkup.provider import _normalize_search_results

        result = _normalize_search_results({
            "results": [
                {"name": "Python Docs", "url": "https://docs.python.org", "content": "Official docs"},
                {"name": "Tutorial", "url": "https://example.com", "content": "A tutorial"},
            ]
        })
        assert result["success"] is True
        assert result["data"]["web"] == [
            {"url": "https://docs.python.org", "title": "Python Docs", "description": "Official docs", "position": 1},
            {"url": "https://example.com", "title": "Tutorial", "description": "A tutorial", "position": 2},
        ]

    def test_missing_and_null_fields_do_not_leak_none(self):
        from plugins.web.linkup.provider import _normalize_search_results

        result = _normalize_search_results({"results": [{"name": None, "content": None}]})
        assert result["data"]["web"] == [
            {"url": "", "title": "", "description": "", "position": 1}
        ]

    def test_absent_results_key_is_empty(self):
        from plugins.web.linkup.provider import _normalize_search_results

        assert _normalize_search_results({})["data"]["web"] == []


class TestTitleFromMarkdown:
    @pytest.mark.parametrize(
        "markdown,expected",
        [
            ("# Hello World\n\nBody text", "Hello World"),
            ("\n\n## Section\ntext", "Section"),
            ("Plain paragraph with no heading", ""),
            ("", ""),
            ("#", ""),
        ],
    )
    def test_leading_heading(self, markdown: str, expected: str):
        from plugins.web.linkup.provider import _title_from_markdown

        assert _title_from_markdown(markdown) == expected


# ─── query parsing ───────────────────────────────────────────────────────────


class TestParseQuery:
    @pytest.mark.parametrize(
        "query,clean,include,exclude",
        [
            ("best laptops site:example.com", "best laptops", ["example.com"], []),
            ("best laptops -site:spam.com", "best laptops", [], ["spam.com"]),
            (
                "ai news site:techcrunch.com -site:medium.com",
                "ai news",
                ["techcrunch.com"],
                ["medium.com"],
            ),
            ("SITE:EXAMPLE.COM news", "news", ["example.com"], []),
            ("-Site:Example.COM", "-Site:Example.COM", [], ["example.com"]),
            ("plain natural query", "plain natural query", [], []),
        ],
    )
    def test_operators_are_lifted_and_stripped(
        self, query: str, clean: str, include: list, exclude: list
    ):
        from plugins.web.linkup.provider import _parse_query

        assert _parse_query(query) == (clean, include, exclude)

    def test_operators_only_keeps_the_original_query(self):
        from plugins.web.linkup.provider import _parse_query

        assert _parse_query("site:only.com") == ("site:only.com", ["only.com"], [])

    def test_repeated_operators_are_deduplicated(self):
        from plugins.web.linkup.provider import _parse_query

        clean, include, exclude = _parse_query("q site:a.com site:A.com -site:b.com -site:b.com")
        assert clean == "q"
        assert include == ["a.com"]
        assert exclude == ["b.com"]

    def test_operator_like_substring_is_not_parsed(self):
        from plugins.web.linkup.provider import _parse_query

        assert _parse_query("docs/site:foo") == ("docs/site:foo", [], [])


class TestNormalizeDomains:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("a.com, b.com", ["a.com", "b.com"]),
            (["a.com", "b.com"], ["a.com", "b.com"]),
            (["a.com", "b.com, C.com"], ["a.com", "b.com", "c.com"]),
            ("a.com, a.com , ", ["a.com"]),
            ("  .a.com.  ", ["a.com"]),
            (None, []),
            ("", []),
            ([], []),
            (123, []),
        ],
    )
    def test_normalization(self, value, expected: list):
        from plugins.web.linkup.provider import _normalize_domains

        assert _normalize_domains(value) == expected


# ─── _post ───────────────────────────────────────────────────────────────────


class TestPost:
    def test_keyed_request_hits_v1_search_with_bearer_auth(self):
        mock_response = _response({"results": []})
        with patch("plugins.web.linkup.provider.httpx.post", return_value=mock_response) as mock_post:
            from plugins.web.linkup.provider import _post

            _post("search", {"q": "test"}, "lk-test-key")

        mock_post.assert_called_once()
        assert mock_post.call_args.args[0] == "https://api.linkup.so/v1/search"
        assert mock_post.call_args.kwargs["headers"]["Authorization"] == "Bearer lk-test-key"
        assert mock_post.call_args.kwargs["json"] == {"q": "test"}

    def test_base_url_override_is_honored(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_BASE_URL", "https://proxy.example/v1/")
        mock_response = _response({"markdown": "hi"})
        with patch("plugins.web.linkup.provider.httpx.post", return_value=mock_response) as mock_post:
            from plugins.web.linkup.provider import _post

            _post("fetch", {"url": "https://example.com"}, "lk-test-key")

        assert mock_post.call_args.args[0] == "https://proxy.example/v1/fetch"

    def test_http_error_raises_with_verbatim_body(self):
        mock_response = _response({}, status_code=401, text="Invalid API key")
        with patch("plugins.web.linkup.provider.httpx.post", return_value=mock_response):
            from plugins.web.linkup.provider import _post

            with pytest.raises(ValueError, match="Invalid API key"):
                _post("search", {"q": "test"}, "bad-key")

    def test_http_error_without_body_falls_back_to_status(self):
        mock_response = _response({}, status_code=503, text="")
        with patch("plugins.web.linkup.provider.httpx.post", return_value=mock_response):
            from plugins.web.linkup.provider import _post

            with pytest.raises(ValueError, match="HTTP 503"):
                _post("search", {"q": "test"}, "lk-test-key")


# ─── search() ────────────────────────────────────────────────────────────────


class TestSearch:
    def test_search_payload_and_envelope(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        mock_response = _response({
            "results": [{"name": "Result", "url": "https://r.com", "content": "desc"}]
        })
        with patch("plugins.web.linkup.provider.httpx.post", return_value=mock_response) as mock_post:
            from plugins.web.linkup.provider import LinkupWebSearchProvider

            result = LinkupWebSearchProvider().search("test query", limit=3)

        assert result["success"] is True
        assert result["data"]["web"][0]["title"] == "Result"
        payload = mock_post.call_args.kwargs["json"]
        assert payload["q"] == "test query"
        assert payload["depth"] == "standard"
        assert payload["outputType"] == "searchResults"
        assert payload["maxResults"] == 3

    def test_limit_is_capped_at_vendor_max(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        with patch("plugins.web.linkup.provider.httpx.post", return_value=_response({"results": []})) as mock_post:
            from plugins.web.linkup.provider import LinkupWebSearchProvider

            LinkupWebSearchProvider().search("q", limit=50)

        assert mock_post.call_args.kwargs["json"]["maxResults"] == 20

    def test_missing_key_fails_without_network_call(self):
        from plugins.web.linkup.provider import LinkupWebSearchProvider

        with patch("plugins.web.linkup.provider.httpx.post") as mock_post:
            result = LinkupWebSearchProvider().search("q")

        mock_post.assert_not_called()
        assert result["success"] is False
        assert "LINKUP_API_KEY" in result["error"]

    def test_network_error_is_captured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        with patch("plugins.web.linkup.provider.httpx.post", side_effect=httpx.ConnectError("boom")):
            from plugins.web.linkup.provider import LinkupWebSearchProvider

            result = LinkupWebSearchProvider().search("q")

        assert result["success"] is False
        assert "boom" in result["error"]


class TestSearchDepth:
    @pytest.mark.parametrize("depth", ["flash", "fast", "standard", "deep"])
    def test_config_depth_reaches_payload(self, monkeypatch: pytest.MonkeyPatch, linkup_config, depth: str):
        linkup_config({"depth": depth})
        assert _search_payload(monkeypatch)["depth"] == depth

    def test_env_depth_used_when_config_absent(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({})
        monkeypatch.setenv("LINKUP_DEPTH", "deep")
        assert _search_payload(monkeypatch)["depth"] == "deep"

    def test_config_depth_beats_env(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"depth": "fast"})
        monkeypatch.setenv("LINKUP_DEPTH", "deep")
        assert _search_payload(monkeypatch)["depth"] == "fast"

    def test_depth_is_case_and_whitespace_insensitive(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"depth": "  DEEP  "})
        assert _search_payload(monkeypatch)["depth"] == "deep"

    def test_unknown_config_depth_falls_back_to_standard(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"depth": "turbo"})
        monkeypatch.setenv("LINKUP_DEPTH", "deep")
        assert _search_payload(monkeypatch)["depth"] == "standard"

    def test_unknown_env_depth_falls_back_to_standard(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({})
        monkeypatch.setenv("LINKUP_DEPTH", "turbo")
        assert _search_payload(monkeypatch)["depth"] == "standard"

    def test_blank_config_depth_falls_through_to_env(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"depth": "   "})
        monkeypatch.setenv("LINKUP_DEPTH", "fast")
        assert _search_payload(monkeypatch)["depth"] == "fast"


class TestSearchDomainFilters:
    def test_config_list_domains_reach_payload(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"include_domains": ["docs.python.org"], "exclude_domains": ["spam.com"]})
        payload = _search_payload(monkeypatch)
        assert payload["includeDomains"] == ["docs.python.org"]
        assert payload["excludeDomains"] == ["spam.com"]

    def test_config_comma_separated_string_domains(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"include_domains": "a.com, b.com", "exclude_domains": "c.com,"})
        payload = _search_payload(monkeypatch)
        assert payload["includeDomains"] == ["a.com", "b.com"]
        assert payload["excludeDomains"] == ["c.com"]

    def test_env_domains_used_when_config_absent(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({})
        monkeypatch.setenv("LINKUP_INCLUDE_DOMAINS", "a.com, b.com")
        monkeypatch.setenv("LINKUP_EXCLUDE_DOMAINS", "c.com")
        payload = _search_payload(monkeypatch)
        assert payload["includeDomains"] == ["a.com", "b.com"]
        assert payload["excludeDomains"] == ["c.com"]

    def test_config_domains_beat_env(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"include_domains": ["config.com"]})
        monkeypatch.setenv("LINKUP_INCLUDE_DOMAINS", "env.com")
        assert _search_payload(monkeypatch)["includeDomains"] == ["config.com"]

    def test_no_domain_keys_when_nothing_is_configured(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({})
        payload = _search_payload(monkeypatch)
        assert "includeDomains" not in payload
        assert "excludeDomains" not in payload

    def test_query_operators_merge_with_configured_domains(self, monkeypatch: pytest.MonkeyPatch, linkup_config):
        linkup_config({"include_domains": ["config.com"], "exclude_domains": ["blocked.com"]})
        payload = _search_payload(monkeypatch, query="news site:query.com -site:junk.com site:config.com")
        assert payload["q"] == "news"
        assert payload["includeDomains"] == ["query.com", "config.com"]
        assert payload["excludeDomains"] == ["junk.com", "blocked.com"]


# ─── extract() ───────────────────────────────────────────────────────────────


class TestExtract:
    def test_one_fetch_per_url_maps_markdown_to_documents(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        responses = [
            _response({"markdown": "# First Page\n\nContent one"}),
            _response({"markdown": "No heading here"}),
        ]
        with patch("plugins.web.linkup.provider.httpx.post", side_effect=responses) as mock_post:
            from plugins.web.linkup.provider import LinkupWebSearchProvider

            docs = LinkupWebSearchProvider().extract(["https://a.example", "https://b.example"])

        assert [call.args[0] for call in mock_post.call_args_list] == [
            "https://api.linkup.so/v1/fetch", "https://api.linkup.so/v1/fetch",
        ]
        assert [call.kwargs["json"] for call in mock_post.call_args_list] == [
            {"url": "https://a.example"}, {"url": "https://b.example"},
        ]
        assert docs[0]["url"] == "https://a.example"
        assert docs[0]["title"] == "First Page"
        assert docs[0]["content"] == "# First Page\n\nContent one"
        assert docs[0]["raw_content"] == "# First Page\n\nContent one"
        assert docs[0]["metadata"]["sourceURL"] == "https://a.example"
        assert docs[1]["title"] == ""

    def test_failed_url_does_not_sink_the_batch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        failure = _response({}, status_code=404, text="Not found")
        with patch("plugins.web.linkup.provider.httpx.post", side_effect=[failure, _response({"markdown": "ok"})]):
            from plugins.web.linkup.provider import LinkupWebSearchProvider

            docs = LinkupWebSearchProvider().extract(["https://gone.example", "https://ok.example"])

        assert docs[0]["url"] == "https://gone.example"
        assert "Not found" in docs[0]["error"]
        assert docs[1]["content"] == "ok"
        assert "error" not in docs[1]

    def test_missing_key_returns_per_url_errors_without_network_call(self):
        from plugins.web.linkup.provider import LinkupWebSearchProvider

        urls = ["https://a.example", "https://b.example"]
        with patch("plugins.web.linkup.provider.httpx.post") as mock_post:
            docs = LinkupWebSearchProvider().extract(urls)

        mock_post.assert_not_called()
        assert [d["url"] for d in docs] == urls
        assert all("LINKUP_API_KEY" in d["error"] for d in docs)


# ─── availability, discovery, dispatch ───────────────────────────────────────


def _ensure_plugins_loaded() -> None:
    from hermes_cli.plugins import _ensure_plugins_discovered

    _ensure_plugins_discovered()


class TestAvailabilityAndRegistration:
    def test_is_available_tracks_the_api_key(self, monkeypatch: pytest.MonkeyPatch):
        from plugins.web.linkup.provider import LinkupWebSearchProvider

        assert LinkupWebSearchProvider().is_available() is False
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        assert LinkupWebSearchProvider().is_available() is True

    def test_discovery_registers_the_provider(self):
        _ensure_plugins_loaded()
        from agent.web_search_registry import get_provider

        provider = get_provider("linkup")
        assert provider is not None
        assert provider.display_name == "Linkup"
        assert provider.supports_search() is True
        assert provider.supports_extract() is True


_BUILTIN_WEB_KEYS = (
    "TAVILY_API_KEY", "TAVILY_BASE_URL", "PERPLEXITY_API_KEY", "EXA_API_KEY", "PARALLEL_API_KEY",
    "KEENABLE_API_KEY", "FIRECRAWL_API_KEY", "FIRECRAWL_API_URL", "FIRECRAWL_GATEWAY_URL",
    "TOOL_GATEWAY_DOMAIN", "TOOL_GATEWAY_USER_TOKEN", "SEARXNG_URL", "BRAVE_SEARCH_API_KEY", "XAI_API_KEY",
)


class TestAutoDetect:
    """A never-configured install holding only LINKUP_API_KEY resolves to Linkup."""

    @pytest.fixture(autouse=True)
    def _no_competing_backends(self, monkeypatch: pytest.MonkeyPatch):
        for key in _BUILTIN_WEB_KEYS:
            monkeypatch.delenv(key, raising=False)
        # ``_get_backend`` probes plugin-contributed providers from the registry, which the real
        # call path populates first (``web_search_tool`` runs discovery before backend resolution).
        _ensure_plugins_loaded()
        with patch("tools.web_tools._is_tool_gateway_ready", return_value=False), \
             patch("tools.web_tools._ddgs_package_importable", return_value=False):
            yield

    def test_backend_autodetect_picks_linkup(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        from tools.web_tools import _get_backend

        assert _get_backend() == "linkup"

    def test_tool_gate_lights_up_for_linkup_only(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        from tools.web_tools import check_web_api_key

        assert check_web_api_key() is True


class TestDispatch:
    def test_web_search_tool_routes_to_linkup(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")
        mock_response = _response({
            "results": [{"name": "Result", "url": "https://r.com", "content": "desc"}]
        })
        with patch("tools.web_tools._get_backend", return_value="linkup"), \
             patch("plugins.web.linkup.provider.httpx.post", return_value=mock_response), \
             patch("tools.interrupt.is_interrupted", return_value=False):
            from tools.web_tools import web_search_tool

            result = json.loads(web_search_tool("test query", limit=3))

        assert result["success"] is True
        assert result["data"]["web"][0] == {
            "url": "https://r.com", "title": "Result", "description": "desc", "position": 1,
        }

    def test_web_extract_tool_routes_to_linkup(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LINKUP_API_KEY", "lk-test-key")

        async def _allow_ssrf(_url: str) -> bool:
            return True

        with patch("tools.web_tools._get_backend", return_value="linkup"), \
             patch("tools.web_tools.async_is_safe_url", _allow_ssrf), \
             patch("plugins.web.linkup.provider.httpx.post",
                   return_value=_response({"markdown": "# Page\n\nExtracted content"})):
            from tools.web_tools import web_extract_tool

            result = json.loads(asyncio.run(web_extract_tool(["https://example.com"])))

        assert result["results"][0]["url"] == "https://example.com"
        assert "Extracted content" in result["results"][0]["content"]
