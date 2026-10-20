"""Request-local subagent visibility preserves profile and source boundaries."""

from pathlib import Path

import pytest


@pytest.fixture
def listing_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agent import secret_scope
    from hermes_cli import profiles, web_server
    from hermes_cli.web_routers import profiles as routes
    from hermes_state import SessionDB

    # Keep both canonical profile discovery and config loading on real, isolated homes.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(root))
    homes = {"default": root, "worker": root / "profiles" / "worker"}
    configs = {}
    for name, home in homes.items():
        home.mkdir(parents=True)
        configs[name] = f"sessions:\n  show_subagents: {str(name == 'worker').lower()}\n"
        (home / "config.yaml").write_text(configs[name], encoding="utf-8")
        db = SessionDB(db_path=home / "state.db")
        try:
            parent = f"{name}-desktop"
            for source in ("desktop", "cron", "telegram", "subagent"):
                sid = f"{name}-{source}"
                delegate = source == "subagent"
                db.create_session(
                    sid, source=source, parent_session_id=parent if delegate else None,
                    model_config={"_delegate_from": parent} if delegate else None,
                )
                db.append_message(session_id=sid, role="user", content="hello")
        finally:
            db.close()

    assert dict(profiles.profiles_to_serve(multiplex=True)) == homes
    secret_scope.set_multiplex_active(True)
    routes._sidebar_profile_cache_clear()
    try:
        # No lifespan: these read routes need no running gateway or network services.
        client = TestClient(web_server.app)
        client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
        yield client, homes
        assert {name: (home / "config.yaml").read_text(encoding="utf-8")
                for name, home in homes.items()} == configs
    finally:
        client.close()
        routes._sidebar_profile_cache_clear()
        secret_scope.set_multiplex_active(False)


def _get(client, path, **params):
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert not payload.get("errors"), payload
    return payload


def _ids(rows):
    return {row["id"] for row in rows}


def test_list_override_preserves_profile_defaults_and_source_slices(listing_client):
    client, homes = listing_client
    for path in ("/api/sessions", "/api/profiles/sessions"):
        # A -> B -> A through real profile resolution/config readers, under multiplex.
        for name in ("default", "worker", "default"):
            stats = _get(client, "/api/sessions/stats", profile=name)
            delegates = _get(client, path, profile=name, source="subagent",
                             include_subagents="true")
            assert stats["by_source"]["subagent"] == delegates["total"]
            for query in (f"{name}-subagent", "hello"):
                hits = _get(client, "/api/sessions/search", profile=name, q=query,
                            source="subagent", include_subagents="true")
                assert {row["session_id"] for row in hits["results"]} == {f"{name}-subagent"}
            roots = {f"{name}-{s}" for s in ("desktop", "cron", "telegram")}
            subagent = {f"{name}-subagent"}
            cases = [
                ({}, roots | (subagent if name == "worker" else set())),
                ({"include_subagents": "false"}, roots),
                ({"include_subagents": "true"}, roots | subagent),
                # Explicit true prunes only the subagent exclusion; cron stays excluded.
                ({"include_subagents": "true", "exclude_sources": "cron,subagent"},
                 roots - {f"{name}-cron"} | subagent),
                ({"include_subagents": "true", "exclude_sources": "cron"},
                 roots - {f"{name}-cron"} | subagent),
                ({"include_subagents": "true", "source": "subagent",
                  "exclude_sources": "subagent"}, subagent),
                ({"include_subagents": "true", "sources": "desktop,subagent"},
                 {f"{name}-desktop"} | subagent),
                ({"include_subagents": "true", "source": "cron"}, {f"{name}-cron"}),
                ({"include_subagents": "true", "sources": "desktop,telegram"},
                 roots - {f"{name}-cron"}),
                ({"include_subagents": "false", "source": "subagent"}, set()),
                # Omission retains the existing policy even for an opted-in profile.
                ({"source": "subagent"}, set()),
                ({"exclude_sources": "cron"}, roots - {f"{name}-cron"}),
            ]
            for params, expected in cases:
                payload = _get(client, path, profile=name, **params)
                assert _ids(payload["sessions"]) == expected, (path, name, params)
                assert payload["total"] == len(expected)
                assert {row["profile"] for row in payload["sessions"]} <= {name}
                if "profile_totals" in payload:
                    assert payload["profile_totals"] == {name: len(expected)}

    for override in (None, "true", "false", None):
        params = {} if override is None else {"include_subagents": override}
        payload = _get(client, "/api/profiles/sessions", **params)
        expected = {f"{name}-{source}" for name in homes
                    for source in ("desktop", "cron", "telegram")}
        expected |= {f"{name}-subagent" for name in homes
                     if override == "true" or (override is None and name == "worker")}
        assert _ids(payload["sessions"]) == expected
        assert payload["total"] == len(expected)


def test_sidebar_override_only_changes_recents_with_both_caches_warm(listing_client, monkeypatch):
    client, homes = listing_client
    from hermes_cli.web_routers import profiles as routes

    # Keep the actual TTL/single-flight and per-profile DB cache active.
    monkeypatch.setattr(routes, "_SIDEBAR_CACHE_TTL_SECONDS", 60.0)
    reads = []
    real_read = routes._read_profile_db

    def read(name, *args, **kwargs):
        reads.append(name)
        return real_read(name, *args, **kwargs)

    monkeypatch.setattr(routes, "_read_profile_db", read)
    for scope in ("default", "worker", "default", "all"):
        names = set(homes) if scope == "all" else {scope}
        for override in (None, "true", "false", "true", None):
            params = {} if override is None else {"include_subagents": override}
            payload = _get(
                client, "/api/profiles/sessions/sidebar", recents_profile=scope,
                recents_exclude="cron,telegram,subagent",
                messaging_exclude="desktop,cron", **params,
            )
            expected = {f"{name}-desktop" for name in names}
            expected |= {f"{name}-subagent" for name in names
                         if override == "true" or (override is None and name == "worker")}
            assert _ids(payload["recents"]["sessions"]) == expected, (scope, override)
            assert _ids(payload["cron"]["sessions"]) == {f"{name}-cron" for name in names}
            assert _ids(payload["messaging"]["sessions"]) == {
                f"{name}-telegram" for name in names}

    params = dict(recents_profile="all", recents_exclude="cron,telegram,subagent",
                  messaging_exclude="desktop,cron", include_subagents="false")
    before = list(reads)
    cached = _get(client, "/api/profiles/sessions/sidebar", **params)
    assert reads == before  # The full-response cache serves the repeated request.

    # Bypass only the outer TTL cache: the profile cache must still serve the same payload.
    # Warm once more because opening/closing SQLite can change its WAL fingerprint.
    monkeypatch.setattr(routes, "_SIDEBAR_CACHE_TTL_SECONDS", 0.0)
    assert _get(client, "/api/profiles/sessions/sidebar", **params) == cached
    before = list(reads)
    assert _get(client, "/api/profiles/sessions/sidebar", **params) == cached
    assert reads == before
