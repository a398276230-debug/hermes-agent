"""Offline contracts for Gemini-native proxy endpoint selection and wire use."""

from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace

import httpx
import pytest

from agent.agent_runtime_helpers import create_openai_client
from agent.gemini_native_adapter import (
    GeminiAPIError,
    GeminiNativeClient,
    build_gemini_request,
    gemini_accepts_parameters_json_schema,
    is_native_gemini_base_url,
)

PROXY = "http://127.0.0.1:18003/v1beta"
MODEL = "gemini-3.8-flash"
KEY = "offline-proxy-key"
TOOLS = [{
    "type": "function",
    "function": {
        "name": "lookup",
        "description": "Look up a fixture value.",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Endpoint tests must use the injected MockTransport")

    with asyncio.Runner() as runner:
        # Windows creates a loopback socketpair for the event loop's wakeup pipe.
        # Initialize only that IPC before blocking all test DNS and connections.
        runner.get_loop()
        monkeypatch.setattr(socket.socket, "connect", forbidden)
        monkeypatch.setattr(socket, "getaddrinfo", forbidden)
        yield runner


@pytest.mark.parametrize("url, expected", [
    ("https://generativelanguage.googleapis.com/v1beta", True),
    ("https://generativelanguage.googleapis.com/v1", True),
    (PROXY, True),
    (PROXY + "/", True),
    ("https://proxy.invalid/gemini/v1beta", True),
    ("http://[::1]:18003/v1beta", True),
    ("https://generativelanguage.googleapis.com/v1beta/openai/", False),
    ("https://generativelanguage.googleapis.com/v1beta/OPENAI/", False),
    (PROXY + "/openai", False),
    ("https://proxy.invalid/v1", False),
    ("https://proxy.invalid/v1beta-other", False),
    ("https://proxy.invalid/v1?upstream=generativelanguage.googleapis.com", False),
    ("https://generativelanguage.googleapis.com.proxy.invalid/v1", False),
    (PROXY + "?key=not-a-base-url", False),
    (PROXY + "#fragment", False),
    (PROXY + "?", False),
    (PROXY + "#", False),
    ("file:///v1beta", False),
    ("http://[invalid/v1beta", False),
    ("", False),
])
def test_native_endpoint_recognition(url, expected):
    assert is_native_gemini_base_url(url) is expected


def _primary_client(base_url, handler, *, provider="gemini", shared=True, api_key=KEY):
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, trust_env=False)
    agent = SimpleNamespace(
        provider=provider,
        _client_log_context=lambda: "offline-gemini-proxy",
    )
    return create_openai_client(
        agent,
        {"api_key": api_key, "base_url": base_url, "http_client": http_client},
        reason="offline_endpoint_test",
        shared=shared,
    )


@pytest.mark.parametrize("shared", [True, False])
@pytest.mark.parametrize("provider", ["gemini", "custom", "custom:proxy"])
def test_primary_factory_selects_native_proxy_and_round_trips_tools(shared, provider):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url == httpx.URL(PROXY + f"/models/{MODEL}:generateContent")
        assert request.headers["x-goog-api-key"] == KEY
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "lookup"
        assert body["systemInstruction"]["parts"] == [{"text": "Keep the system stable."}]
        if len(requests) == 1:
            parts = [{
                "functionCall": {"id": "call_proxy_1", "name": "lookup", "args": {"key": "a"}},
                "thoughtSignature": "fixture-signature",
            }]
        else:
            assert body["contents"][-2]["parts"][0]["thoughtSignature"] == "fixture-signature"
            assert body["contents"][-1]["parts"] == [{
                "functionResponse": {
                    "id": "call_proxy_1",
                    "name": "lookup",
                    "response": {"value": "fixture-value"},
                },
            }]
            parts = [{"text": "The value is fixture-value."}]
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 3, "totalTokenCount": 13},
        })

    history = [
        {"role": "system", "content": "Keep the system stable."},
        {"role": "user", "content": "Look up a."},
    ]
    with _primary_client(PROXY, respond, shared=shared, provider=provider) as client:
        assert isinstance(client, GeminiNativeClient)
        response = client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS)
        assert response.choices[0].finish_reason == "tool_calls"
        call = response.choices[0].message.tool_calls[0]
        history.extend([
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": call.id,
                "type": call.type,
                "function": {"name": call.function.name, "arguments": call.function.arguments},
                "extra_content": call.extra_content,
            }]},
            {"role": "tool", "tool_call_id": call.id, "content": '{"value":"fixture-value"}'},
        ])
        final = client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS)
        assert final.choices[0].message.content == "The value is fixture-value."
        assert final.usage.total_tokens == 13
    assert len(requests) == 2
    assert client.is_closed


@pytest.mark.parametrize("provider, base_url", [
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/"),
    ("gemini", "https://proxy.invalid/v1"),
    ("custom", "https://proxy.invalid/v1"),
    ("custom:proxy", "https://proxy.invalid/v1"),
    ("custom:proxy", PROXY + "/openai"),
    ("custom:proxy", PROXY + "?route=test"),
    ("custom:proxy", PROXY + "?"),
    ("custom:proxy", PROXY + "#"),
])
def test_openai_compatibility_routes_still_select_openai(provider, base_url):
    from openai import OpenAI

    with _primary_client(base_url, lambda request: httpx.Response(500), provider=provider) as client:
        assert isinstance(client, OpenAI)


def test_native_proxy_stream_uses_sse_and_preserves_tool_signature():
    def respond(request):
        assert request.url.path == f"/v1beta/models/{MODEL}:streamGenerateContent"
        assert dict(request.url.params) == {"alt": "sse"}
        assert request.headers["accept"] == "text/event-stream"
        assert request.headers["x-goog-api-key"] == KEY
        body = json.loads(request.content)
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "lookup"
        payload = {"candidates": [{
            "content": {"parts": [{
                "functionCall": {"id": "call_stream_1", "name": "lookup", "args": {"key": "a"}},
                "thoughtSignature": "stream-signature",
            }]},
            "finishReason": "STOP",
        }], "usageMetadata": {"totalTokenCount": 7}}
        return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n",
                              headers={"content-type": "text/event-stream"})

    with _primary_client(PROXY, respond) as client:
        assert isinstance(client, GeminiNativeClient)
        chunks = list(client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "Look up a."}],
            tools=TOOLS, stream=True,
        ))
        call = chunks[0].choices[0].delta.tool_calls[0]
        assert call.id == "call_stream_1"
        assert call.extra_content["google"]["thought_signature"] == "stream-signature"
        assert chunks[-1].choices[0].finish_reason == "tool_calls"
        assert chunks[-1].usage.total_tokens == 7


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status, code", [
    (400, "gemini_http_400"),
    (401, "gemini_unauthorized"),
    (429, "gemini_rate_limited"),
    (500, "gemini_http_500"),
])
@pytest.mark.parametrize("provider", ["gemini", "custom:proxy"])
def test_proxy_errors_remain_structured_without_hidden_retry(status, code, stream, provider):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "fixture failure"}},
                              headers={"Retry-After": "2"})

    with _primary_client(PROXY, respond, provider=provider) as client:
        assert isinstance(client, GeminiNativeClient)
        with pytest.raises(GeminiAPIError) as error:
            result = client.chat.completions.create(
                model=MODEL, messages=[{"role": "user", "content": "hi"}], stream=stream,
            )
            if stream:
                list(result)
        assert error.value.status_code == status
        assert error.value.code == code
        assert error.value.retry_after == 2
    assert len(requests) == 1


@pytest.fixture
def proxy_runtime(tmp_path, monkeypatch):
    from hermes_cli.runtime_provider import resolve_runtime_provider

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    (tmp_path / "config.yaml").write_text(
        "model:\n"
        f"  provider: gemini\n  default: {MODEL}\n  base_url: {PROXY}\n",
        encoding="utf-8",
    )
    runtime = resolve_runtime_provider()
    assert runtime["provider"] == "gemini"
    assert runtime["base_url"] == PROXY
    assert runtime["api_key"] == KEY
    assert runtime["api_mode"] == "chat_completions"
    return {**runtime, "model": MODEL}


@pytest.fixture
def mock_native_http(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url == httpx.URL(PROXY + f"/models/{MODEL}:generateContent")
        assert request.headers["x-goog-api-key"] == KEY
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "offline answer"}]},
                            "finishReason": "STOP"}],
        })

    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request",
        lambda self, request: transport.handle_request(request),
    )
    return requests


def test_config_runtime_constructs_real_agent_and_reserves_compression_budget(
    proxy_runtime, mock_native_http, monkeypatch,
):
    from agent import context_compressor, model_metadata
    from agent.gemini_native_adapter import GEMINI_DEFAULT_MAX_OUTPUT_TOKENS
    from run_agent import AIAgent

    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: [])
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    monkeypatch.setattr(model_metadata, "get_model_context_length", lambda *a, **k: 131072)
    monkeypatch.setattr(context_compressor, "get_model_context_length", lambda *a, **k: 131072)
    monkeypatch.setattr(model_metadata, "detect_local_server_type", lambda *a, **k: None)
    agent = AIAgent(
        model=MODEL, provider=proxy_runtime["provider"],
        api_key=proxy_runtime["api_key"], base_url=proxy_runtime["base_url"],
        api_mode=proxy_runtime["api_mode"], quiet_mode=True,
        skip_context_files=True, skip_memory=True,
    )
    try:
        assert isinstance(agent.client, GeminiNativeClient)
        assert agent.context_compressor.max_tokens == GEMINI_DEFAULT_MAX_OUTPUT_TOKENS
        reply = agent.client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": "hi"}],
        )
        assert reply.choices[0].message.content == "offline answer"
        assert len(mock_native_http) == 1
    finally:
        agent.client.close()


@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("async_mode", [False, True])
def test_auxiliary_explicit_and_main_runtime_routes_use_native_proxy(
    proxy_runtime, mock_native_http, no_network, auto, async_mode,
):
    from agent.auxiliary_client import resolve_provider_client
    from agent.gemini_native_adapter import AsyncGeminiNativeClient

    kwargs = {"main_runtime": proxy_runtime, "task": "compression"} if auto else {
        "explicit_base_url": proxy_runtime["base_url"],
        "explicit_api_key": proxy_runtime["api_key"],
    }
    client, model = resolve_provider_client(
        "auto" if auto else "gemini", model=MODEL, async_mode=async_mode, **kwargs,
    )
    assert model == MODEL
    expected_type = AsyncGeminiNativeClient if async_mode else GeminiNativeClient
    assert isinstance(client, expected_type)
    assert client.base_url == PROXY

    async def call_async():
        try:
            return await client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "summarize"}],
            )
        finally:
            await client.close()

    if async_mode:
        response = no_network.run(call_async())
    else:
        with client:
            response = client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "summarize"}],
            )
    assert response.choices[0].message.content == "offline answer"
    assert len(mock_native_http) == 1


@pytest.mark.parametrize("base_url, studio", [
    ("http://127.0.0.1:9/v1beta", False),
    ("https://proxy.invalid/gemini/v1beta", False),
    ("https://generativelanguage.googleapis.com.proxy.invalid/v1beta", False),
    ("https://proxy.invalid/generativelanguage.googleapis.com/v1beta", False),
    ("https://generativelanguage.googleapis.com/v1beta", True),
])
@pytest.mark.parametrize("signature", [None, "opaque+/signature==", "skip_thought_signature_validator"])
def test_tool_signature_policy_follows_endpoint(base_url, studio, signature):
    call = {"id": "call_history", "type": "function", "function": {
        "name": "lookup", "arguments": '{"key":"a"}',
    }}
    if signature is not None:
        call["extra_content"] = {"google": {"thought_signature": signature}}
    history = [
        {"role": "user", "content": "Look up a."},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call_history", "content": '{"value":"a"}'},
    ]
    original = json.dumps(history)
    requests = []

    def respond(request):
        requests.append(request)
        part = json.loads(request.content)["contents"][1]["parts"][0]
        if signature and signature != "skip_thought_signature_validator":
            assert part["thoughtSignature"] == signature
        elif studio:
            assert part["thoughtSignature"] == "skip_thought_signature_validator"
        else:
            assert "thoughtSignature" not in part
            assert b"skip_thought_signature_validator" not in request.content
        return httpx.Response(200, json={"candidates": [{
            "content": {"parts": [{"text": "done"}]}, "finishReason": "STOP",
        }]})

    with _primary_client(base_url, respond) as client:
        client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS)
    assert len(requests) == 1
    assert json.dumps(history) == original


@pytest.fixture
def custom_runtimes(tmp_path, monkeypatch):
    import yaml
    from hermes_cli.runtime_provider import resolve_runtime_provider

    channels = [
        ("vertex_fixture", "http://127.0.0.1:9/v1beta", "VERTEX_FIXTURE_KEY", "offline-vertex", MODEL),
        ("agy_fixture", "https://agy.invalid/gemini/v1beta", "AGY_FIXTURE_KEY", "offline-agy", MODEL + "-high"),
    ]
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    providers = {}
    for name, base_url, key_env, key, model in channels:
        monkeypatch.setenv(key_env, key)
        providers[name] = {"api": base_url, "key_env": key_env, "default_model": model}
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "model": {"provider": "custom:vertex_fixture", "default": MODEL},
        "providers": providers,
        "agent": {"reasoning_effort": "high"},
    }), encoding="utf-8")
    runtimes = []
    for name, base_url, _, key, model in channels:
        runtime = resolve_runtime_provider(requested="custom:" + name, target_model=model)
        assert runtime["requested_provider"] == "custom:" + name
        assert runtime["provider"] == "custom"
        assert runtime["api_mode"] == "chat_completions"
        assert runtime["base_url"] == base_url
        assert runtime["api_key"] == key
        assert runtime["model"] == model
        runtimes.append(runtime)
    assert resolve_runtime_provider()["api_key"] == runtimes[0]["api_key"]
    return runtimes


def test_two_custom_native_channels_keep_separate_credentials_and_models(custom_runtimes):
    requests = []
    for runtime in [*custom_runtimes, custom_runtimes[0]]:
        def respond(request):
            requests.append(request)
            assert str(request.url) == runtime["base_url"] + f'/models/{runtime["model"]}:generateContent'
            assert request.headers["x-goog-api-key"] == runtime["api_key"]
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"candidates": [{
                "content": {"parts": [{"text": runtime["model"]}]}, "finishReason": "STOP",
            }]})

        with _primary_client(runtime["base_url"], respond, provider=runtime["provider"],
                             api_key=runtime["api_key"]) as client:
            assert isinstance(client, GeminiNativeClient)
            response = client.chat.completions.create(
                model=runtime["model"], messages=[{"role": "user", "content": "hi"}],
            )
            assert response.choices[0].message.content == runtime["model"]
    assert len(requests) == 3


@pytest.mark.parametrize("with_profile", [False, True])
@pytest.mark.parametrize("reasoning, expected", [
    ({"enabled": True, "effort": "high"}, {"includeThoughts": True, "thinkingLevel": "high"}),
    ({"enabled": False}, {"includeThoughts": False, "thinkingBudget": 0}),
    (None, None),
])
def test_custom_native_thinking_reaches_wire(with_profile, reasoning, expected):
    from agent.transports.chat_completions import ChatCompletionsTransport
    from providers import get_provider_profile

    transport = ChatCompletionsTransport()
    kwargs = transport.build_kwargs(
        MODEL, [{"role": "user", "content": "hi"}],
        provider_profile=get_provider_profile("custom") if with_profile else None,
        provider_name="custom:proxy", base_url=PROXY, reasoning_config=reasoning,
    )
    assert kwargs.get("extra_body", {}).get("thinking_config") == expected

    def respond(request):
        expected_wire = dict(expected) if expected else expected
        if expected_wire and expected_wire.get("thinkingLevel"):
            expected_wire["thinkingLevel"] = expected_wire["thinkingLevel"].upper()
        assert json.loads(request.content)["generationConfig"].get("thinkingConfig") == expected_wire
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    # Isolate the kwargs contract from the factory routing contract above.
    with GeminiNativeClient(api_key=KEY, base_url=PROXY,
                            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        client.chat.completions.create(**kwargs)

    compat = transport.build_kwargs(
        MODEL, [{"role": "user", "content": "hi"}],
        provider_profile=get_provider_profile("custom") if with_profile else None,
        provider_name="custom:proxy", base_url="https://proxy.invalid/v1", reasoning_config=reasoning,
    )
    assert "thinking_config" not in compat.get("extra_body", {})


@pytest.mark.parametrize("route", ["named", "explicit", "auto"])
@pytest.mark.parametrize("async_mode", [False, True])
def test_custom_auxiliary_native_routes(custom_runtimes, monkeypatch, no_network, route, async_mode):
    from agent.auxiliary_client import resolve_provider_client, _build_call_kwargs
    from agent.gemini_native_adapter import AsyncGeminiNativeClient

    requests = []
    for runtime in custom_runtimes:
        def respond(request):
            requests.append(request)
            assert str(request.url) == runtime["base_url"] + f'/models/{runtime["model"]}:generateContent'
            assert request.headers["x-goog-api-key"] == runtime["api_key"]
            assert json.loads(request.content)["generationConfig"]["thinkingConfig"] == {
                "includeThoughts": True, "thinkingLevel": "HIGH",
            }
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

        mock = httpx.MockTransport(respond)
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", mock.handle_request)
        provider = runtime["requested_provider"]
        options = {}
        if route == "explicit":
            provider = "custom"
            options = {"explicit_base_url": runtime["base_url"], "explicit_api_key": runtime["api_key"]}
        elif route == "auto":
            provider = "auto"
            options = {"main_runtime": runtime, "task": "compression"}
        client, model = resolve_provider_client(provider, model=runtime["model"],
                                                async_mode=async_mode, **options)
        assert model == runtime["model"]
        assert isinstance(client, AsyncGeminiNativeClient if async_mode else GeminiNativeClient)
        kwargs = _build_call_kwargs(
            runtime["requested_provider"], model, [{"role": "user", "content": "hi"}],
            reasoning_config={"enabled": True, "effort": "high"}, base_url=runtime["base_url"],
            native_gemini=True,
        )

        async def call_async():
            try:
                return await client.chat.completions.create(**kwargs)
            finally:
                await client.close()

        if async_mode:
            response = no_network.run(call_async())
        else:
            with client:
                response = client.chat.completions.create(**kwargs)
        assert response.choices[0].message.content == "ok"
    assert len(requests) == 2


@pytest.mark.parametrize("stream", [False, True])
def test_auxiliary_aggregate_and_persisted_history_replay_real_signature(stream):
    from agent.auxiliary_client import _aggregate_chat_stream
    from agent.transports.chat_completions import ChatCompletionsTransport

    signature = "opaque+/stream-or-json=="
    history = [{"role": "user", "content": "Look up a."}]
    requests = []

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        assert b"skip_thought_signature_validator" not in request.content
        if len(requests) == 1:
            parts = [{"functionCall": {"id": "signed-call", "name": "lookup", "args": {"key": "a"}},
                      "thoughtSignature": signature}]
        else:
            assert body["contents"][-2]["parts"][0]["thoughtSignature"] == signature
            assert body["contents"][-1]["parts"][0]["functionResponse"] == {
                "id": "signed-call", "name": "lookup", "response": {"value": "fixture"},
            }
            parts = [{"text": "done"}]
        payload = {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
        if stream:
            return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload)

    with GeminiNativeClient(api_key=KEY, base_url="http://127.0.0.1:9/v1beta",
                            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        response = client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS, stream=stream)
        if stream:
            response = _aggregate_chat_stream(response, model=MODEL)
        normalized = ChatCompletionsTransport().normalize_response(response)
        call = normalized.tool_calls[0]
        assert call.extra_content == {"google": {"thought_signature": signature}}
        history.extend([
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": call.id, "type": call.type,
                "function": {"name": call.name, "arguments": call.arguments},
                **call.provider_data,
            }]},
            {"role": "tool", "tool_call_id": call.id, "content": '{"value":"fixture"}'},
        ])
        history = json.loads(json.dumps(history))
        kwargs = ChatCompletionsTransport().build_kwargs(MODEL, history, TOOLS)
        response = client.chat.completions.create(**kwargs, stream=stream)
        if stream:
            response = _aggregate_chat_stream(response, model=MODEL)
        assert response.choices[0].message.content == "done"
    assert len(requests) == 2


@pytest.mark.parametrize("stream", [False, True])
def test_custom_native_agent_executes_signed_tool_loop(custom_runtimes, monkeypatch, stream):
    from agent import context_compressor, model_metadata
    from run_agent import AIAgent

    runtime = custom_runtimes[0]
    monkeypatch.setattr("model_tools.get_tool_definitions", lambda *a, **k: TOOLS)
    monkeypatch.setattr("model_tools.check_toolset_requirements", lambda *a, **k: {})
    monkeypatch.setattr(model_metadata, "get_model_context_length", lambda *a, **k: 131072)
    monkeypatch.setattr(context_compressor, "get_model_context_length", lambda *a, **k: 131072)
    monkeypatch.setattr(model_metadata, "detect_local_server_type", lambda *a, **k: None)
    executions = []
    requests = []

    def lookup(name, arguments, task_id=None, **kwargs):
        executions.append((name, arguments))
        return '{"value":"fixture-value"}'

    monkeypatch.setattr("model_tools.handle_function_call", lookup)

    def respond(request):
        requests.append(request)
        assert request.headers["x-goog-api-key"] == runtime["api_key"]
        assert request.url.path.endswith(":streamGenerateContent" if stream else ":generateContent")
        body = json.loads(request.content)
        assert body["tools"][0]["functionDeclarations"][0]["name"] == "lookup"
        assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"
        assert b"skip_thought_signature_validator" not in request.content
        if len(requests) == 1:
            parts = [{"functionCall": {"id": "agent-call", "name": "lookup", "args": {"key": "a"}},
                      "thoughtSignature": "agent-signature+/=="}]
        else:
            assert len(requests) == 2
            call_parts = [p for c in body["contents"] for p in c["parts"] if "functionCall" in p]
            assert call_parts[-1]["thoughtSignature"] == "agent-signature+/=="
            results = [p["functionResponse"] for c in body["contents"] for p in c["parts"]
                       if "functionResponse" in p]
            assert results[-1]["response"] == {"value": "fixture-value"}
            parts = [{"text": "The value is fixture-value."}]
        payload = {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
        if stream:
            return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload)

    mock = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", mock.handle_request)
    agent = AIAgent(
        model=runtime["model"], provider=runtime["provider"], api_key=runtime["api_key"],
        base_url=runtime["base_url"], api_mode=runtime["api_mode"], max_iterations=3,
        reasoning_config={"enabled": True, "effort": "high"},
        quiet_mode=True, skip_context_files=True, skip_memory=True,
    )
    try:
        assert isinstance(agent.client, GeminiNativeClient)
        agent._disable_streaming = not stream
        result = agent.run_conversation("Look up a.")
        assert result["final_response"] == "The value is fixture-value."
        assert executions == [("lookup", {"key": "a"})]
        assert len(requests) == 2
        saved_calls = [tc for msg in result["messages"] for tc in msg.get("tool_calls", [])]
        assert saved_calls[-1]["extra_content"]["google"]["thought_signature"] == "agent-signature+/=="
    finally:
        agent.client.close()


@pytest.mark.parametrize("route", ["named", "explicit"])
@pytest.mark.parametrize("suffix", ["/v1", "/v1beta/openai", "/v1beta?version=1",
                                   "/v1beta?flag", "/v1beta?", "/v1beta#fragment", "/v1beta#"])
def test_custom_auxiliary_openai_routes_do_not_become_native(tmp_path, monkeypatch, route, suffix):
    import yaml
    from openai import OpenAI
    from agent.auxiliary_client import resolve_provider_client

    base_url = "https://proxy.invalid" + suffix
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("PROXY_FIXTURE_KEY", KEY)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "model": {"provider": "custom:fixture", "default": MODEL},
        "providers": {"fixture": {"api": base_url, "key_env": "PROXY_FIXTURE_KEY"}},
    }), encoding="utf-8")
    if route == "named":
        client, _ = resolve_provider_client("custom:fixture", model=MODEL)
    else:
        client, _ = resolve_provider_client("custom", model=MODEL,
                                            explicit_base_url=base_url, explicit_api_key=KEY)
    with client:
        assert isinstance(client, OpenAI)


@pytest.mark.parametrize("field", ["thinking_config", "thinkingConfig"])
@pytest.mark.parametrize("source", ["additions", "overrides", "auxiliary"])
def test_custom_native_explicit_thinking_config_is_preserved(field, source):
    from agent.auxiliary_client import _build_call_kwargs
    from agent.transports.chat_completions import ChatCompletionsTransport
    from providers import get_provider_profile

    configured = {"includeThoughts": False}
    reasoning = {"enabled": True, "effort": "high"}
    messages = [{"role": "user", "content": "hi"}]
    if source == "auxiliary":
        kwargs = _build_call_kwargs("custom:proxy", MODEL, messages, base_url=PROXY,
                                    reasoning_config=reasoning, extra_body={field: configured}, native_gemini=True)
    else:
        options = {"extra_body_additions": {field: configured}} if source == "additions" else {
            "request_overrides": {"extra_body": {field: configured}},
        }
        kwargs = ChatCompletionsTransport().build_kwargs(
            MODEL, messages, provider_profile=get_provider_profile("custom"),
            base_url=PROXY, reasoning_config=reasoning, **options,
        )

    def respond(request):
        assert json.loads(request.content)["generationConfig"]["thinkingConfig"] == configured
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    with GeminiNativeClient(api_key=KEY, base_url=PROXY,
                            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        client.chat.completions.create(**kwargs)


# Upstream parametersJsonSchema stays intact on the newly routed proxy path.

@pytest.mark.parametrize("url, expected", [
    ("https://generativelanguage.googleapis.com/v1beta", True),
    (PROXY, True),
    (PROXY + "/", True),
    ("http://127.0.0.1:8050/v1beta", True),
    ("https://generativelanguage.googleapis.com/v1", False),
    ("https://generativelanguage.googleapis.com/v1alpha", False),
    ("http://127.0.0.1:8050/v1", False),
    ("", False),
])
def test_parameters_json_schema_surface_recognition(url, expected):
    assert gemini_accepts_parameters_json_schema(url) is expected


def test_build_gemini_request_tool_dialect_flag():
    legacy = build_gemini_request(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS,
        model=MODEL, tools_as_json_schema=False,
    )
    full = build_gemini_request(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS,
        model=MODEL, tools_as_json_schema=True,
    )
    legacy_decl = legacy["tools"][0]["functionDeclarations"][0]
    full_decl = full["tools"][0]["functionDeclarations"][0]
    assert legacy_decl["parameters"]["properties"]["key"] == {"type": "string"}
    assert "parametersJsonSchema" not in legacy_decl
    assert full_decl["parametersJsonSchema"]["properties"]["key"] == {"type": "string"}
    assert "parameters" not in full_decl


@pytest.mark.parametrize("base_url", [PROXY, "http://127.0.0.1:8050/v1beta"])
def test_native_proxy_sends_parameters_json_schema_on_v1beta(base_url):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        })

    history = [{"role": "user", "content": "hi"}]
    with _primary_client(base_url, respond, provider="custom") as client:
        assert isinstance(client, GeminiNativeClient)
        client.chat.completions.create(model=MODEL, messages=history, tools=TOOLS)
    decl = json.loads(requests[0].content)["tools"][0]["functionDeclarations"][0]
    assert decl["name"] == "lookup"
    assert decl["parametersJsonSchema"]["properties"]["key"] == {"type": "string"}
    assert "parameters" not in decl


def test_thinking_level_uppercased_on_wire():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
        })

    history = [{"role": "user", "content": "hi"}]
    with _primary_client(PROXY, respond, provider="custom") as client:
        client.chat.completions.create(
            model=MODEL, messages=history,
            extra_body={"thinking_config": {"includeThoughts": True, "thinkingLevel": "high"}},
        )
    body = json.loads(requests[0].content)
    assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("suffix", ["", "?version=1", "?"])
def test_auxiliary_request_thinking_follows_selected_wire(tmp_path, monkeypatch, no_network, async_mode, suffix):
    import yaml
    from agent import auxiliary_client as aux

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("PROXY_FIXTURE_KEY", KEY)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "providers": {"query_fixture": {"api": PROXY + suffix, "key_env": "PROXY_FIXTURE_KEY"}},
    }), encoding="utf-8")
    requests = []

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        if not suffix:
            payload = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
            if request.url.path.endswith(":streamGenerateContent"):
                return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n",
                                      headers={"content-type": "text/event-stream"})
            return httpx.Response(200, json=payload)
        if body.get("stream"):
            payload = {"id": "fixture", "object": "chat.completion.chunk", "created": 0, "model": MODEL,
                       "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}]}
            return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 0, "model": MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        })

    mock = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", mock.handle_request)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", mock.handle_async_request)
    kwargs = {"provider": "custom:query_fixture", "model": MODEL,
              "messages": [{"role": "user", "content": "hi"}],
              "reasoning_config": {"enabled": True, "effort": "high"}}
    aux.shutdown_cached_clients()
    try:
        response = no_network.run(aux.async_call_llm(**kwargs)) if async_mode else aux.call_llm(**kwargs)
        assert response.choices[0].message.content == "ok"
        assert len(requests) == 1
        request = requests[0]
        body = json.loads(request.content)
        if suffix:
            assert request.headers["authorization"] == f"Bearer {KEY}"
            assert "x-goog-api-key" not in request.headers
            assert "thinking_config" not in body
            assert "thinkingConfig" not in body
            assert body["reasoning_effort"] == "high"
        else:
            assert request.url.path.startswith("/v1beta/models/")
            assert body["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "HIGH"
    finally:
        aux.shutdown_cached_clients()


@pytest.mark.parametrize("route", ["primary", "auxiliary", "async_auxiliary"])
@pytest.mark.parametrize("stream", [False, True])
def test_custom_native_key_cmd_refreshes_on_each_request(tmp_path, monkeypatch, no_network, route, stream):
    import yaml
    from hermes_cli.runtime_provider import resolve_runtime_provider
    from agent.auxiliary_client import resolve_provider_client

    tokens = iter(["offline-first-token", "offline-second-token"])
    calls = []

    def token_provider():
        token = next(tokens)
        calls.append(token)
        return token

    def command_source(command, label):
        assert command == "offline-fixture-command"
        return token_provider

    monkeypatch.setattr("agent.command_token_source.build_command_token_provider", command_source)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "model": {"provider": "custom:command_fixture", "default": MODEL},
        "providers": {"command_fixture": {"api": PROXY, "key_cmd": "offline-fixture-command"}},
    }), encoding="utf-8")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["x-goog-api-key"] == calls[-1]
        assert "authorization" not in request.headers
        assert request.url.path.endswith(":streamGenerateContent" if stream else ":generateContent")
        payload = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}
        if stream:
            return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload)

    mock = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", mock.handle_request)
    if route == "primary":
        runtime = resolve_runtime_provider()
        client = _primary_client(PROXY, respond, provider=runtime["provider"], api_key=runtime["api_key"])
    else:
        client, _ = resolve_provider_client("custom:command_fixture", model=MODEL,
                                            async_mode=route == "async_auxiliary")
    assert calls == []
    kwargs = {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "stream": stream}

    async def call_async_twice():
        try:
            for _ in range(2):
                response = await client.chat.completions.create(**kwargs)
                if stream:
                    assert "".join([chunk.choices[0].delta.content or "" async for chunk in response]) == "ok"
                else:
                    assert response.choices[0].message.content == "ok"
        finally:
            await client.close()

    if route == "async_auxiliary":
        no_network.run(call_async_twice())
    else:
        with client:
            for _ in range(2):
                response = client.chat.completions.create(**kwargs)
                if stream:
                    assert "".join(chunk.choices[0].delta.content or "" for chunk in response) == "ok"
                else:
                    assert response.choices[0].message.content == "ok"
    assert calls == ["offline-first-token", "offline-second-token"]
    assert len(requests) == 2


@pytest.mark.parametrize("native_first", [False, True])
@pytest.mark.parametrize("stage", ["credential_retry", "fallback"])
def test_auxiliary_recovery_thinking_follows_rebuilt_client(monkeypatch, native_first, stage):
    from openai import OpenAI
    from agent import auxiliary_client as aux

    def respond(request):
        raise AssertionError("Request preparation must not send HTTP")

    with GeminiNativeClient(api_key=KEY, base_url=PROXY,
                            http_client=httpx.Client(transport=httpx.MockTransport(respond))) as native:
        with OpenAI(api_key=KEY, base_url=PROXY, default_query={"version": "1"},
                    http_client=httpx.Client(transport=httpx.MockTransport(respond))) as openai:
            first, rebuilt = (native, openai) if native_first else (openai, native)
            common = dict(
                task=None, messages=[{"role": "user", "content": "hi"}], temperature=None,
                max_tokens=None, tools=None, effective_timeout=30.0, effective_extra_body={},
                reasoning_config={"enabled": True, "effort": "high"},
            )

            def assert_wire(kwargs, is_native):
                extra = kwargs.get("extra_body", {})
                assert ("thinking_config" in extra) is is_native
                if is_native:
                    assert extra["thinking_config"]["thinkingLevel"] == "high"

            if stage == "credential_retry":
                monkeypatch.setattr(aux, "_get_cached_client", lambda *a, **k: (first, MODEL))
                client, kwargs = aux._prepare_same_provider_retry(
                    resolved_provider="custom:proxy", resolved_model=MODEL, resolved_base_url=PROXY,
                    resolved_api_key=KEY, resolved_api_mode=None, main_runtime=None, final_model=MODEL,
                    async_mode=False, **common,
                )
                assert client is first
                assert_wire(kwargs, native_first)
            else:
                destination = aux._FallbackDestination("custom:proxy", PROXY, "chat_completions", MODEL)
                monkeypatch.setattr(aux, "_fallback_entry_timeout", lambda *a: None)
                monkeypatch.setattr(aux, "_fallback_destination", lambda *a: destination)
                monkeypatch.setattr(aux, "_fallback_chain_entry", lambda *a: None)
                _, kwargs, rebuild = aux._plan_fallback_candidate(
                    first, MODEL, "custom:proxy", apply_fast_lane=False, **common,
                )
                assert_wire(kwargs, native_first)
                _, rebuilt_kwargs = rebuild("custom:proxy", rebuilt, MODEL)
                assert_wire(rebuilt_kwargs, not native_first)
