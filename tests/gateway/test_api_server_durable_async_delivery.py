"""Opt-in async delivery for DURABLE-HISTORY API clients (#50319 follow-up).

Upstream #50319 made the API server's stateless path stop *silently* promising async
delivery: ``APIServerAdapter.supports_async_delivery = False`` forces
``notify_on_complete`` off, because the adapter has no push channel. But a client that
supplies an explicit ``X-Hermes-Session-Id`` and reads
``GET /api/sessions/{id}/messages`` IS a consumer the completion can be written to —
exactly the contract ``delegate_task`` already honours through
``session_history_delivery_supported()``.

This module pins the two halves of the resulting contract:

* ``platforms.api_server.extra.async_delivery`` (or ``API_SERVER_ASYNC_DELIVERY``)
  opts the deployment in, and ``_async_delivery_binding`` grants the capability per
  request only when that request ALSO declared a durable-history consumer.
* A wake turn (``gateway/wake.py`` self-post) is stamped as machinery
  (``internal_notification``) so a durable client can identify the detached delivery,
  and the self-post carries ``hermes_wake_turn`` for the request that asks for it.
"""

import asyncio

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from gateway.session_context import (
    async_delivery_supported,
    clear_session_vars,
    get_session_env,
)


def _adapter(**extra):
    return APIServerAdapter(PlatformConfig(enabled=True, extra=extra))


class TestAsyncDeliveryBinding:
    """The capability is opt-in twice: deployment AND declared history consumer."""

    def test_off_by_default(self):
        """A stock API server keeps upstream #50319 behaviour, even with a session id."""
        adapter = _adapter()
        assert adapter._async_delivery_for_history is False
        assert adapter._async_delivery_binding("1") is False
        assert adapter._async_delivery_binding(True) is False

    def test_opt_in_requires_explicit_history_consumer(self):
        adapter = _adapter(async_delivery=True)
        assert adapter._async_delivery_binding("1") is True
        assert adapter._async_delivery_binding(True) is True
        # Fingerprint-derived id: no declared consumer can read a delivery.
        assert adapter._async_delivery_binding("") is False
        assert adapter._async_delivery_binding(None) is False
        assert adapter._async_delivery_binding(False) is False

    def test_env_opt_in(self, monkeypatch):
        monkeypatch.setenv("API_SERVER_ASYNC_DELIVERY", "1")
        assert _adapter()._async_delivery_for_history is True
        monkeypatch.setenv("API_SERVER_ASYNC_DELIVERY", "false")
        assert _adapter()._async_delivery_for_history is False

    def test_extra_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("API_SERVER_ASYNC_DELIVERY", "1")
        assert _adapter(async_delivery=False)._async_delivery_for_history is False


class TestSessionBinding:
    """Bound sessions report the capability the tools gate on."""

    def test_durable_history_client_gets_async_delivery(self):
        adapter = _adapter(async_delivery=True)
        sid = "qq_private_3054039169_20260922_1"
        tokens = adapter._bind_api_server_session(
            chat_id=sid, session_key=sid, session_id=sid,
            session_history_delivery="1",
            async_delivery=adapter._async_delivery_binding("1"))
        try:
            assert async_delivery_supported() is True
            # Routing identity is unchanged: the raw session id stays the chat id.
            assert get_session_env("HERMES_SESSION_PLATFORM") == "api_server"
            assert get_session_env("HERMES_SESSION_CHAT_ID") == sid
        finally:
            clear_session_vars(tokens)

    def test_default_binding_is_still_push_disabled(self):
        adapter = _adapter(async_delivery=True)
        tokens = adapter._bind_api_server_session(
            chat_id="s1", session_key="s1", session_id="s1", session_history_delivery="")
        try:
            assert async_delivery_supported() is False
        finally:
            clear_session_vars(tokens)


class _FakeAgent:
    """Duck-typed agent that records the conversation kwargs it was called with."""

    provider = "fake"
    model = "fake-model"

    def __init__(self):
        self.kwargs = {}

    def run_conversation(self, **kwargs):
        self.kwargs = kwargs
        return {"final_response": "ok", "messages": [], "api_calls": 0, "tools": []}


def _run_turn(monkeypatch, adapter, **run_kwargs):
    agent = _FakeAgent()
    monkeypatch.setattr(adapter, "_create_agent", lambda **kwargs: agent)
    result, _usage = asyncio.run(adapter._run_agent(conversation_history=[], **run_kwargs))
    assert result["final_response"] == "ok"
    return agent


class TestWakeTurnIsMachinery:
    def test_wake_turn_marks_the_persisted_user_row(self, monkeypatch):
        """A self-posted wake turn is machinery, not a client prompt: durable clients
        (and the transcript UI) must be able to tell it apart from a real user turn."""
        adapter = _adapter()
        agent = _run_turn(
            monkeypatch, adapter, user_message="[IMPORTANT: Background process proc_x exited]",
            session_id="s1", session_history_delivery="1", wake_turn=True)
        assert agent.kwargs["persist_user_display_kind"] == "internal_notification"

    def test_normal_turn_is_not_marked(self, monkeypatch):
        adapter = _adapter()
        agent = _run_turn(
            monkeypatch, adapter, user_message="hello", session_id="s1",
            session_history_delivery="1")
        assert "persist_user_display_kind" not in agent.kwargs

    def test_wake_turn_keeps_async_delivery_for_opted_in_deployment(self, monkeypatch):
        """A wake turn that spawns its own background work keeps the capability."""
        adapter = _adapter(async_delivery=True)
        bound = {}

        class _BindingAgent(_FakeAgent):
            def run_conversation(self, **kwargs):
                bound["supported"] = async_delivery_supported()
                bound["platform"] = get_session_env("HERMES_SESSION_PLATFORM")
                return super().run_conversation(**kwargs)

        monkeypatch.setattr(adapter, "_create_agent", lambda **kwargs: _BindingAgent())
        asyncio.run(adapter._run_agent(
            user_message="[IMPORTANT: x]", conversation_history=[], session_id="s1",
            session_history_delivery="1", wake_turn=True))
        assert bound == {"supported": True, "platform": "api_server"}


class TestWakeSelfPostCarriesMarker:
    def test_self_post_body_marks_a_wake_turn(self):
        """``gateway.wake._self_post_chat_completion`` asks the route to stamp the turn."""
        from aiohttp import web

        from gateway.wake import deliver_wake

        seen = {}

        async def handler(request):
            seen["body"] = await request.json()
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        class _ApiAdapter:
            supports_async_delivery = False
            _host = "127.0.0.1"
            _api_key = "sekrit"
            _model_name = "hermes-agent"

            def __init__(self, port):
                self._port = port

        async def run():
            app = web.Application()
            app.router.add_post("/v1/chat/completions", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            try:
                await deliver_wake(_ApiAdapter(port), text="[IMPORTANT: done]", session_id="sid-1")
            finally:
                await runner.cleanup()

        asyncio.run(run())
        assert seen["body"]["hermes_wake_turn"] is True
        assert seen["body"]["stream"] is False


class TestOpenAiRouteHonoursWakeMarker:
    """``POST /v1/chat/completions`` marks the turn only for the self-post marker."""

    @staticmethod
    def _app(adapter):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer

        app = web.Application()
        app["api_server_adapter"] = adapter
        app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
        return TestClient(TestServer(app))

    @pytest.mark.asyncio
    async def test_marker_sets_wake_turn(self, monkeypatch):
        from unittest.mock import patch

        adapter = _adapter(key="sk-secret")
        captured = {}

        async def _mock_run_agent(**kwargs):
            captured.update(kwargs)
            return ({"final_response": "ok", "messages": [], "api_calls": 1},
                    {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

        async with self._app(adapter) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                resp = await cli.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "[IMPORTANT: done]"}],
                          "hermes_wake_turn": True},
                    headers={"Authorization": "Bearer sk-secret",
                             "X-Hermes-Session-Id": "qq_private_1_20260922_1"},
                )
        assert resp.status == 200
        assert captured.get("wake_turn") is True
        assert captured.get("session_history_delivery") == "1"

    @pytest.mark.asyncio
    async def test_absent_marker_leaves_call_shape_unchanged(self):
        from unittest.mock import patch

        adapter = _adapter(key="sk-secret")
        captured = {}

        async def _mock_run_agent(**kwargs):
            captured.update(kwargs)
            return ({"final_response": "ok", "messages": [], "api_calls": 1},
                    {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

        async with self._app(adapter) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                resp = await cli.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": "Bearer sk-secret",
                             "X-Hermes-Session-Id": "qq_private_1_20260922_1"},
                )
        assert resp.status == 200
        assert "wake_turn" not in captured

    @pytest.mark.asyncio
    async def test_marker_without_session_id_is_ignored(self):
        """A header-less client cannot stamp a turn: only the audited self-post can."""
        from unittest.mock import patch

        adapter = _adapter(key="sk-secret")
        captured = {}

        async def _mock_run_agent(**kwargs):
            captured.update(kwargs)
            return ({"final_response": "ok", "messages": [], "api_calls": 1},
                    {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})

        async with self._app(adapter) as cli:
            with patch.object(adapter, "_run_agent", side_effect=_mock_run_agent):
                resp = await cli.post(
                    "/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": "hi"}],
                          "hermes_wake_turn": True},
                    headers={"Authorization": "Bearer sk-secret"},
                )
        assert resp.status == 200
        assert "wake_turn" not in captured
        assert captured.get("session_history_delivery") == ""


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
def test_truthy_env_values(monkeypatch, value):
    monkeypatch.setenv("API_SERVER_ASYNC_DELIVERY", value)
    assert _adapter()._async_delivery_for_history is True
