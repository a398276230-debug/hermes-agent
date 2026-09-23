"""LIVE Windows E2E for background-executor spawn parity (#70716 / PR salvage).

Runs ONLY on a real Windows host (the on-demand ``windows-venv-e2e.yml``
lane). The systemd cgroup-isolation feature for local background executors
must be a strict no-op on Windows: jobs spawn exactly as before, output is
captured, exit codes are correct, and no systemd code path is ever reached
— even when the process claims gateway identity.

These tests drive the REAL ``ProcessRegistry.spawn_local`` pipe path on the
live Windows process table (real Popen, real Git Bash shell, real reader
thread) — no mocked spawn.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

# The ``windows_only`` marker (not a bare ``skipif``) is what makes the OS lane's
# file selector import this module — see scripts/ci/list_os_marked_tests.py.
pytestmark = pytest.mark.windows_only


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    import tools.process_registry as pr

    reg = pr.ProcessRegistry()
    yield reg
    for sid in list(reg._running):
        try:
            reg.kill_process(sid)
        except Exception:
            pass


def _wait_exit(reg, sid, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        sess = reg._finished.get(sid) or reg._running.get(sid)
        if sess is not None and sess.exited:
            return sess
        time.sleep(0.2)
    raise AssertionError(f"session {sid} did not exit within {timeout}s")


class TestWindowsSpawnParity:
    def test_background_job_runs_output_and_exit_code_unchanged(self, registry):
        """Plain background job: spawned, output captured, exit code correct."""
        session = registry.spawn_local("echo win-live-parity; exit 7")
        done = _wait_exit(registry, session.id)

        assert done.exit_code == 7
        assert "win-live-parity" in done.output_buffer
        # The systemd scope identity must never be recorded on Windows.
        assert done.systemd_unit == ""

    def test_gateway_identity_never_reaches_systemd_path_on_windows(
        self, registry, monkeypatch
    ):
        """Even with full (faked) gateway identity, the Windows spawn takes
        the legacy path: no scope argv is built, no probe runs, and the job
        behaves exactly as without the identity."""
        import tools.process_registry as pr

        monkeypatch.setenv("_HERMES_GATEWAY", "1")
        monkeypatch.setattr(
            "gateway.status.get_running_pid",
            lambda *, cleanup_stale=False: os.getpid(),
        )
        monkeypatch.setattr(
            "gateway.restart.is_gateway_supervisor_process", lambda: True
        )
        monkeypatch.setattr(pr, "_SYSTEMD_SCOPE_AVAILABLE", None)

        scope_builds = []
        monkeypatch.setattr(
            pr,
            "_build_systemd_scope_argv",
            lambda *a, **k: scope_builds.append(a) or a[0],
        )

        session = registry.spawn_local("echo win-live-gateway; exit 3")
        done = _wait_exit(registry, session.id)

        assert done.exit_code == 3
        assert "win-live-gateway" in done.output_buffer
        assert done.systemd_unit == ""
        assert scope_builds == [], "Windows must never build a systemd scope argv"
        # The availability probe must not have flipped to True on Windows.
        assert pr._SYSTEMD_SCOPE_AVAILABLE is not True

    def test_kill_process_windows_plain_path(self, registry):
        """kill_process on Windows works without any systemd unit cleanup."""
        session = registry.spawn_local("sleep 60")
        time.sleep(1.0)
        result = registry.kill_process(session.id)
        assert result.get("status") in {"killed", "already_exited"}
        assert session.systemd_unit == ""


class TestWindowsOrphanedPipe:
    """A grandchild that outlives the direct child holds our stdout write handle.

    Windows pipes have no ``select()`` and a bare ``read1()`` blocks until every
    write handle closes, so the reader used to park forever — and it parked while
    holding the stream's buffer lock, so the ``stdout.close()`` on the finish path
    deadlocked the tool thread calling ``process_manage kill``/``poll``. Live E2E:
    real Git Bash, real grandchild, real reader thread (#68915).
    """

    @staticmethod
    def _bounded(call, label: str, timeout: float = 15):
        """Run *call* on a worker thread and fail rather than hang on a deadlock."""
        out = []
        worker = threading.Thread(target=lambda: out.append(call()), daemon=True)
        worker.start()
        worker.join(timeout=timeout)
        assert not worker.is_alive(), (
            f"registry.{label}() deadlocked on stdout.close(); the reader is parked "
            "on the orphaned pipe"
        )
        return out[0]

    def test_orphaned_grandchild_pipe_does_not_park_reader_or_deadlock_poll(
        self, registry
    ):
        # ``disown`` leaves the backgrounded sleep as an orphan that still holds the
        # inherited stdout pipe; the shell itself exits immediately.
        session = registry.spawn_local("( sleep 20 ) & disown; exit 0")
        deadline = time.time() + 20
        while time.time() < deadline and session.process.poll() is None:
            time.sleep(0.1)
        assert session.process.poll() is not None, "direct child should exit at once"

        # poll() reconciles the exited direct child and closes the pipe — the exact
        # path that deadlocked while the reader held the buffer lock.
        polled = self._bounded(lambda: registry.poll(session.id), "poll")
        assert polled["status"] == "exited", polled

        # kill() shares the stream teardown; its verdict varies with the race against
        # the reader, but it must always come back.
        killed = self._bounded(lambda: registry.kill_process(session.id), "kill")
        assert killed.get("status") in {"killed", "already_exited", "error"}, killed

        # The reader itself must have finished rather than lingering parked.
        deadline = time.time() + 10
        while time.time() < deadline and session._reader_thread.is_alive():
            time.sleep(0.05)
        assert not session._reader_thread.is_alive(), "reader thread never exited"

    def test_windows_pipe_poll_reports_data_idle_and_broken(self):
        """The readiness poll is what makes a non-blocking read possible: it must
        report waiting bytes without consuming them, idle as 0, and a closed write
        end as the read-anyway sentinel (so the read surfaces EOF)."""
        from tools.environments.base_output import windows_pipe_poll

        r, w = os.pipe()
        try:
            poll = windows_pipe_poll(r)
            assert poll is not None
            assert poll() == 0  # quiet pipe: sleeps briefly, reports idle

            os.write(w, b"hello")
            assert poll() > 0
            assert os.read(r, 4096) == b"hello"

            os.close(w)
            assert poll() != 0  # broken pipe → attempt the read
            assert os.read(r, 4096) == b""  # EOF
        finally:
            os.close(r)
