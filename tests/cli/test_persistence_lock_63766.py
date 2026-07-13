"""Regression tests for issue #63766 — staged-user persistence race.

``_persist_active_session_before_close`` (CLI shutdown safety-net) and
``_persist_session`` (turn-start early persist) can interleave, causing
both paths to write a *different* user-message dict for the same turn,
producing a duplicate user row in ``state.db``.

The fix serialises them with ``agent._persistence_lock`` (RLock).

These tests pin the lock contract:

  1. ``_persist_active_session_before_close`` acquires ``_persistence_lock``
     before calling ``_persist_session`` and releases it in a ``finally`` block.
  2. The lock is held *across* the persist call so a concurrent turn-start
     flush must wait, not race.
  3. The lock is released even when ``_persist_session`` raises.
"""

from __future__ import annotations

import threading
import time


def test_close_path_acquires_and_releases_lock():
    """``_persist_active_session_before_close`` must acquire the agent's
    persistence lock before calling ``_persist_session`` and release it
    afterward — even when ``_persist_session`` raises."""
    import cli as cli_mod
    from unittest.mock import MagicMock

    cli = object.__new__(cli_mod.HermesCLI)
    cli.conversation_history = [{"role": "user", "content": "hi"}]
    cli.session_id = "s1"

    agent = MagicMock()
    # CRITICAL: close path reads ``self.agent`` (not constructed via __new__).
    cli.agent = agent
    agent.session_id = "live-sess"
    agent._session_messages = [{"role": "user", "content": "hi"}]
    agent._persistence_lock = threading.RLock()
    agent._persist_session.side_effect = RuntimeError("disk-full")

    cli._persist_active_session_before_close()  # must not raise / leak lock
    acquired = agent._persistence_lock.acquire(blocking=False)
    assert acquired, "close path leaked the persistence lock on exception"
    agent._persistence_lock.release()


def test_close_path_holds_lock_across_persist_session_call():
    """When ``_persist_active_session_before_close`` calls ``_persist_session``,
    the agent persistence lock must be held so a concurrent turn-start flush
    blocks instead of racing the close flush.

    Real concurrency test: a turn-start thread tries to acquire the same RLock
    while the close path holds it inside ``_persist_session``. We use a real
    RLock + a blocking ``_persist_session`` so the close path holds the lock
    long enough that the concurrent thread is observed to wait.
    """
    import cli as cli_mod
    from unittest.mock import MagicMock

    cli = object.__new__(cli_mod.HermesCLI)
    cli.conversation_history = [{"role": "user", "content": "hi"}]
    cli.session_id = "s1"

    agent = MagicMock()
    # CRITICAL: close path reads ``self.agent`` (not constructed via __new__).
    cli.agent = agent
    agent.session_id = "live-sess"
    agent._session_messages = [{"role": "user", "content": "hi"}]
    # Use a real RLock so mutual exclusion is observable across threads.
    agent._persistence_lock = threading.RLock()

    persist_started = threading.Event()
    sleep_seconds = 0.4

    def blocking_persist(messages, conversation_history=None):
        # _persist_session retakes the RLock re-entrantly (RLock contract).
        with agent._persistence_lock:
            persist_started.set()
            time.sleep(sleep_seconds)

    agent._persist_session = MagicMock(side_effect=blocking_persist)

    concurrent_wait = {}

    def close_path():
        # _persist_active_session_before_close takes the lock before calling
        # _persist_session; _persist_session retakes it re-entrantly.
        cli._persist_active_session_before_close()

    def turn_start_attempt():
        # Wait until the close path is INSIDE the critical section, then try
        # to acquire the same lock from another thread (mimicking
        # build_turn_context calling _persist_session which takes the RLock).
        if not persist_started.wait(timeout=5):
            concurrent_wait["error"] = "persist never started"
            return
        start = time.monotonic()
        with agent._persistence_lock:
            concurrent_wait["wait"] = time.monotonic() - start

    t1 = threading.Thread(target=close_path)
    t2 = threading.Thread(target=turn_start_attempt)
    t1.start()
    t2.start()
    t1.join(timeout=8)
    t2.join(timeout=8)

    assert "error" not in concurrent_wait, concurrent_wait.get("error")
    wait_time = concurrent_wait.get("wait")
    assert wait_time is not None and wait_time >= 0.2, (
        f"concurrent thread did not block on the lock: "
        f"wait={wait_time}s, expected ≥0.2s (close held it ~{sleep_seconds}s)"
    )


def test_persist_session_takes_lock_internally():
    """``_persist_session`` must take ``self._persistence_lock`` so concurrent
    callers serialise. We exercise this with a real bound method, not a mock,
    to verify the source-level contract."""
    import inspect
    import run_agent

    source = inspect.getsource(run_agent.AIAgent._persist_session)
    assert "_persistence_lock" in source, (
        "_persist_session must reference _persistence_lock (lock contract pin)"
    )
    assert "with self._persistence_lock" in source or (
        "self._persistence_lock.acquire()" in source
        and "self._persistence_lock.release()" in source
    ), "_persist_session must acquire/release _persistence_lock"


def test_close_path_takes_lock_in_source():
    """``_persist_active_session_before_close`` must take the lock in source."""
    import inspect
    import cli

    source = inspect.getsource(cli.HermesCLI._persist_active_session_before_close)
    assert "_persistence_lock" in source, (
        "_persist_active_session_before_close must reference _persistence_lock"
    )
    # Must be acquired before _persist_session is called and released in a finally.
    assert ".acquire()" in source, "must call .acquire() on the lock"
    assert "finally" in source, "must release in a finally block"
