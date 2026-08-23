import json
from unittest.mock import patch

from tools import meme_search_tool
from toolsets import resolve_toolset


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_search_memes_returns_bridge_candidates_and_marker_guidance():
    payload = {
        "results": [
            {
                "id": "m_123",
                "tag": "疑惑",
                "category": "问号",
                "description": "没听懂",
                "keywords": ["疑问", "困惑"],
                "score": 4,
            }
        ]
    }

    with patch.object(meme_search_tool, "urlopen", return_value=_Response(payload)) as mocked:
        result = json.loads(meme_search_tool.search_memes_tool("困惑", 3))

    assert result["results"] == payload["results"]
    assert "&&meme:ID&&" in result["usage"]
    requested_url = mocked.call_args.args[0].full_url
    assert requested_url.startswith("http://127.0.0.1:29998/api/memes/search?")
    assert "q=%E5%9B%B0%E6%83%91" in requested_url
    assert "limit=3" in requested_url


def test_search_memes_clamps_limit_to_ten():
    with patch.object(
        meme_search_tool,
        "urlopen",
        return_value=_Response({"results": []}),
    ) as mocked:
        result = json.loads(meme_search_tool.search_memes_tool("笑", 99))

    assert result["results"] == []
    assert "limit=10" in mocked.call_args.args[0].full_url


def test_non_loopback_config_is_rejected(monkeypatch):
    monkeypatch.setattr(
        meme_search_tool,
        "_load_config",
        lambda: {"base_url": "https://example.com"},
    )

    result = json.loads(meme_search_tool.search_memes_tool("笑"))

    assert "loopback" in result["error"]


def test_tool_is_registered_in_service_gated_toolset():
    entry = meme_search_tool.registry.get_entry("search_memes")

    assert entry is not None
    assert entry.toolset == "meme_search"
    assert entry.check_fn is meme_search_tool.check_meme_search_requirements


def test_api_server_toolset_includes_search_memes():
    assert "search_memes" in resolve_toolset(
        "hermes-api-server",
        include_registry=False,
    )
