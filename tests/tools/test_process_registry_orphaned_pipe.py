"""The background-process stdout drain must never park on an orphaned pipe.

Windows regression (#68915): ``read1()`` on a pipe blocks until *every* write
handle closes, and a backgrounded grandchild inherits ours, so EOF may never
arrive. The reader then held the stream's buffer lock forever, which made the
``stdout.close()`` on the finish path (``_release_finished_handles``) deadlock the
tool thread calling ``process_manage kill``/``poll`` — the reported hard hang where
no tool result ever comes back.

``_drain_reader_stream`` is the platform-neutral control flow shared by the POSIX
``select()`` reader and the Windows ``PeekNamedPipe`` reader. These tests drive it
with fake readiness probes, so the *policy* (stop after the post-exit idle window;
never block a read on a quiet pipe) is verified on every host instead of only on
Windows. The live Windows counterpart lives in
``tests/tools/test_process_registry_windows_live.py``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time

import pytest

from tools.process_registry import (
    ProcessRegistry,
    ProcessSession,
    _READER_IDLE_POLLS_AFTER_EXIT,
    _drain_reader_stream,
)

IDLE_LIMIT = _READER_IDLE_POLLS_AFTER_EXIT


def _noop(_chunk: str) -> None:
    pass


def test_orphaned_pipe_is_abandoned_without_a_blocking_read():
    """Direct child exited, pipe quiet, writer still open: stop, don't read.

    A read here is the blocking ``read1()`` that parks the reader thread — and,
    with it, the buffer lock the finish path needs to close the stream.
    """
    polls = []

    def wait_readable():
        polls.append(1)
        return False  # quiet: no bytes, no EOF

    def read_once():
        raise AssertionError("read_once must not run while the pipe is idle")

    _drain_reader_stream(read_once, wait_readable, lambda: 0, _noop)

    assert len(polls) == IDLE_LIMIT, "the idle window must be bounded, not endless"


def test_quiet_pipe_of_a_live_child_is_waited_out():
    """A live (but momentarily quiet) child is never abandoned — only EOF ends it."""
    state = {"probes": 0, "reads": 0}

    def wait_readable():
        state["probes"] += 1
        return state["probes"] > 10  # quiet for well past the idle window

    def read_once():
        state["reads"] += 1
        return "late output\n" if state["reads"] == 1 else None

    emitted = []
    _drain_reader_stream(read_once, wait_readable, lambda: None, emitted.append)

    assert emitted == ["late output\n"]
    assert state["reads"] == 2  # the chunk, then the EOF read
    assert state["probes"] > IDLE_LIMIT + 1


def test_new_output_resets_the_idle_window():
    """Output arriving after the child exited restarts the idle count.

    Without the reset, a burst that trails the exit by one poll would be dropped.
    """
    probes = [False] * (IDLE_LIMIT - 1) + [True] + [False] * IDLE_LIMIT
    calls = {"n": 0}

    def wait_readable():
        value = probes[calls["n"]]
        calls["n"] += 1
        return value

    def read_once():
        return "trailing tail\n" if calls["n"] == len(probes) - IDLE_LIMIT else None

    emitted = []
    _drain_reader_stream(read_once, wait_readable, lambda: 0, emitted.append)

    assert emitted == ["trailing tail\n"]
    assert calls["n"] == len(probes)  # abandoned only after IDLE_LIMIT *fresh* idles


def test_eof_ends_the_loop_immediately():
    """A readable pipe that reports EOF (all writers closed) ends the drain."""
    reads = []

    def read_once():
        reads.append(1)
        return None

    _drain_reader_stream(read_once, lambda: True, lambda: 0, _noop)

    assert reads == [1]


def test_closed_fd_ends_the_loop_without_reading():
    """A probe that raises (fd closed under us) stops the drain instead of crashing it."""
    reads = []

    def wait_readable():
        raise OSError(9, "Bad file descriptor")

    _drain_reader_stream(lambda: reads.append(1) or None, wait_readable, lambda: 0, _noop)

    assert reads == []


def test_empty_chunk_is_not_output():
    """``""`` is a partial multibyte tail and None is EOF; neither is emitted as text."""
    probes = iter([True, True, True])
    reads = iter(["", "hi\n", None])
    emitted = []

    _drain_reader_stream(lambda: next(reads), lambda: next(probes), lambda: None, emitted.append)

    assert emitted == ["hi\n"]


def test_idle_window_is_configurable():
    """The bound is a parameter so the drain can be tuned without touching the loop."""
    polls = []

    def wait_readable():
        polls.append(1)
        return False

    _drain_reader_stream(lambda: None, wait_readable, lambda: 0, _noop, idle_limit=5)

    assert len(polls) == 5


@pytest.mark.linux_only
def test_posix_background_reader_stops_when_a_grandchild_holds_the_pipe():
    """The live POSIX counterpart to the Windows E2E: the reader thread must
    terminate once the direct child is gone and the pipe goes idle.

    A disowned grandchild inherits the stdout write end, so EOF never arrives.
    This is the reference behavior the Windows ``PeekNamedPipe`` path mirrors; if
    the reader parked here, the finish path's ``stdout.close()`` would deadlock
    exactly as it did on Windows.
    """
    proc = subprocess.Popen(
        ["sh", "-c", "( sleep 30 ) & disown; exit 0"],
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        preexec_fn=os.setsid,
    )
    registry = ProcessRegistry()
    session = ProcessSession(
        id="proc_posix_orphan_reader",
        command="( sleep 30 ) & disown; exit 0",
        task_id="t1",
        started_at=time.time(),
    )
    session.process = proc
    session.pid = proc.pid
    registry._running[session.id] = session

    reader = threading.Thread(target=registry._reader_loop, args=(session,), daemon=True)
    reader.start()
    try:
        reader.join(timeout=10)
        assert not reader.is_alive(), (
            "reader parked on the orphaned pipe instead of stopping after the "
            "direct child exited"
        )
        assert session.exited is True
        assert session.exit_code == 0
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
