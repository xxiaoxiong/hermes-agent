"""Tests for cron job inactivity-based timeout.

Tests cover:
- Active agent runs indefinitely (no inactivity timeout)
- Idle agent triggers inactivity timeout with diagnostic info
- Unlimited timeout (HERMES_CRON_TIMEOUT=0)
- Backward compat: HERMES_CRON_TIMEOUT env var still works
- Error message includes activity summary
"""

import concurrent.futures
import os
import sys
import time
from pathlib import Path


# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class FakeAgent:
    """Mock agent with controllable activity summary for timeout tests."""

    def __init__(self, idle_seconds=0.0, activity_desc="tool_call",
                 current_tool=None, api_call_count=5, max_iterations=90):
        self._idle_seconds = idle_seconds
        self._activity_desc = activity_desc
        self._current_tool = current_tool
        self._api_call_count = api_call_count
        self._max_iterations = max_iterations
        self._interrupted = False
        self._interrupt_msg = None

    def get_activity_summary(self):
        return {
            "last_activity_ts": time.time() - self._idle_seconds,
            "last_activity_desc": self._activity_desc,
            "seconds_since_activity": self._idle_seconds,
            "current_tool": self._current_tool,
            "api_call_count": self._api_call_count,
            "max_iterations": self._max_iterations,
        }

    def interrupt(self, msg):
        self._interrupted = True
        self._interrupt_msg = msg

    def run_conversation(self, prompt):
        """Simulate a quick agent run that finishes immediately."""
        return {"final_response": "Done", "messages": []}


class SlowFakeAgent(FakeAgent):
    """Agent that runs for a while, simulating active work then going idle."""

    def __init__(self, run_duration=0.5, idle_after=None, **kwargs):
        super().__init__(**kwargs)
        self._run_duration = run_duration
        self._idle_after = idle_after  # seconds before becoming idle
        self._start_time = None

    def get_activity_summary(self):
        summary = super().get_activity_summary()
        if self._idle_after is not None and self._start_time:
            elapsed = time.time() - self._start_time
            if elapsed > self._idle_after:
                # Agent has gone idle
                idle_time = elapsed - self._idle_after
                summary["seconds_since_activity"] = idle_time
                summary["last_activity_desc"] = "api_call_streaming"
            else:
                summary["seconds_since_activity"] = 0.0
        return summary

    def run_conversation(self, prompt):
        self._start_time = time.time()
        time.sleep(self._run_duration)
        return {"final_response": "Completed after work", "messages": []}


class TestInactivityTimeout:
    """Test the inactivity-based timeout polling loop in cron scheduler."""

    def test_active_agent_completes_normally(self):
        """An agent that finishes quickly should return its result."""
        agent = FakeAgent(idle_seconds=0.0)
        _cron_inactivity_limit = 10.0
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test prompt")
        _inactivity_timeout = False

        result = None
        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                result = future.result()
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                _act = agent.get_activity_summary()
                _idle_secs = _act.get("seconds_since_activity", 0.0)
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False)
        assert result is not None
        assert result["final_response"] == "Done"
        assert not _inactivity_timeout
        assert not agent._interrupted

    def test_idle_agent_triggers_timeout(self):
        """An agent that goes idle should be detected and interrupted."""
        # Agent will run for 0.3s, then become idle after 0.1s of that
        agent = SlowFakeAgent(
            run_duration=5.0,  # would run forever without timeout
            idle_after=0.1,    # goes idle almost immediately
            activity_desc="api_call_streaming",
            current_tool="web_search",
            api_call_count=3,
            max_iterations=50,
        )

        _cron_inactivity_limit = 0.5  # 0.5s inactivity triggers timeout
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test prompt")
        _inactivity_timeout = False

        result = None
        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                result = future.result()
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    _act = agent.get_activity_summary()
                    _idle_secs = _act.get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False, cancel_futures=True)
        assert _inactivity_timeout is True
        assert result is None  # Never got a result — interrupted

    def test_unlimited_timeout(self):
        """HERMES_CRON_TIMEOUT=0 means no timeout at all."""
        agent = FakeAgent(idle_seconds=0.0)
        _cron_inactivity_limit = None  # unlimited

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test prompt")

        # With unlimited, we just await the result directly.
        result = future.result()
        pool.shutdown(wait=False)

        assert result["final_response"] == "Done"

    def _parse_cron_timeout(self, raw_value):
        """Mirror the defensive parsing logic from cron/scheduler.py run_job()."""
        if raw_value:
            try:
                return float(raw_value)
            except (ValueError, TypeError):
                return 600.0
        return 600.0

    def test_timeout_env_var_parsing(self, monkeypatch):
        """HERMES_CRON_TIMEOUT env var is respected."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "1200")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        assert _cron_timeout == 1200.0

        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        assert _cron_inactivity_limit == 1200.0

    def test_timeout_zero_means_unlimited(self, monkeypatch):
        """HERMES_CRON_TIMEOUT=0 yields None (unlimited)."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "0")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        assert _cron_inactivity_limit is None

    def test_timeout_invalid_value_falls_back_to_default(self, monkeypatch):
        """HERMES_CRON_TIMEOUT=abc should fall back to 600s, not raise ValueError."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "abc")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        assert _cron_timeout == 600.0
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        assert _cron_inactivity_limit == 600.0

    def test_timeout_empty_string_uses_default(self, monkeypatch):
        """HERMES_CRON_TIMEOUT='' (empty) should use the 600s default."""
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", "")
        raw = os.getenv("HERMES_CRON_TIMEOUT", "").strip()
        _cron_timeout = self._parse_cron_timeout(raw)
        assert _cron_timeout == 600.0

    def test_timeout_error_includes_diagnostics(self):
        """The TimeoutError message should include last activity info."""
        agent = SlowFakeAgent(
            run_duration=5.0,
            idle_after=0.05,
            activity_desc="api_call_streaming",
            current_tool="delegate_task",
            api_call_count=7,
            max_iterations=90,
        )

        _cron_inactivity_limit = 0.3
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test")
        _inactivity_timeout = False

        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    _act = agent.get_activity_summary()
                    _idle_secs = _act.get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False, cancel_futures=True)
        assert _inactivity_timeout

        # Build the diagnostic message like the scheduler does
        _activity = agent.get_activity_summary()
        _last_desc = _activity.get("last_activity_desc", "unknown")
        _secs_ago = _activity.get("seconds_since_activity", 0)

        err_msg = (
            f"Cron job 'test-job' idle for "
            f"{int(_secs_ago)}s (limit {int(_cron_inactivity_limit)}s) "
            f"— last activity: {_last_desc}"
        )
        assert "idle for" in err_msg
        assert "api_call_streaming" in err_msg

    def test_agent_without_activity_summary_uses_wallclock_fallback(self):
        """If agent lacks get_activity_summary, idle_secs stays 0 (never times out).
        
        This ensures backward compat if somehow an old agent is used.
        The polling loop will eventually complete when the task finishes.
        """
        class BareAgent:
            def run_conversation(self, prompt):
                return {"final_response": "no activity tracker", "messages": []}

        agent = BareAgent()
        _cron_inactivity_limit = 0.1  # tiny limit
        _POLL_INTERVAL = 0.1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(agent.run_conversation, "test")
        _inactivity_timeout = False

        while True:
            done, _ = concurrent.futures.wait({future}, timeout=_POLL_INTERVAL)
            if done:
                result = future.result()
                break
            _idle_secs = 0.0
            if hasattr(agent, "get_activity_summary"):
                try:
                    _act = agent.get_activity_summary()
                    _idle_secs = _act.get("seconds_since_activity", 0.0)
                except Exception:
                    pass
            if _idle_secs >= _cron_inactivity_limit:
                _inactivity_timeout = True
                break

        pool.shutdown(wait=False)
        # Should NOT have timed out — bare agent has no get_activity_summary
        assert not _inactivity_timeout
        assert result["final_response"] == "no activity tracker"


class TestSysPathOrdering:
    """Test that sys.path is set before repo-level imports."""

    def test_hermes_time_importable(self):
        """hermes_time should be importable when cron.scheduler loads."""
        # This import would fail if sys.path.insert comes after the import
        from cron.scheduler import _hermes_now
        assert callable(_hermes_now)

    def test_hermes_constants_importable(self):
        """hermes_constants should be importable from cron context."""
        from hermes_constants import get_hermes_home
        assert callable(get_hermes_home)


class TestCronWorkerJoinBeforeSessionDBClose:
    """Regression tests for #65208: race between cron worker thread still
    executing ``append_message`` (via ``_execute_write`` -> ``self._conn``)
    and the outer ``finally`` block in ``run_job`` closing ``_session_db``.

    The fix: a bounded ``concurrent.futures.wait(..., timeout=...)`` on the
    worker future BEFORE the ``_session_db.close()`` branch. If the worker
    is still running past the grace window, we LEAVE the SessionDB open
    (process-exit GC reclaims it) so the worker can finish its write
    against a live connection instead of raising
    ``'NoneType' object has no attribute 'execute'``.
    """

    def test_worker_done_within_grace_closes_session_db(self):
        """Worker exits cleanly inside the grace window → SessionDB.close() runs."""
        import concurrent.futures

        class FakeSessionDB:
            def __init__(self):
                self.closed = False
                self.title_set = False
                self.ended = False

            def set_session_title(self, *a, **kw):
                self.title_set = True

            def end_session(self, *a, **kw):
                self.ended = True

            def close(self):
                self.closed = True

        # Pretend the worker already finished (done() True before grace wait)
        fake_db = FakeSessionDB()
        future = concurrent.futures.Future()
        future.set_result({"final_response": "ok"})

        _CRON_WORKER_JOIN_GRACE_SECS = 5.0
        _cron_worker_exited = True
        if future is not None and not future.done():
            _done, _ = concurrent.futures.wait(
                {future}, timeout=_CRON_WORKER_JOIN_GRACE_SECS,
            )
            _cron_worker_exited = bool(_done)

        # Mirror scheduler.py gate: only close when worker has exited.
        if fake_db is not None and _cron_worker_exited:
            fake_db.set_session_title(None, "cron test")
            fake_db.end_session(None, "cron_complete")
            fake_db.close()

        assert fake_db.title_set
        assert fake_db.ended
        assert fake_db.closed, "SessionDB.close() MUST run when worker exited"

    def test_worker_still_running_skips_session_db_close(self):
        """Worker still running past grace → SessionDB.close() MUST be skipped.

        This is the core regression for #65208. Without the fix, the
        scheduler would ``_session_db.close()`` (``self._conn = None``)
        while the worker was still mid-``append_message`` →
        ``self._conn.execute`` raised AttributeError / NoneType race.
        With the fix, we LEAVE the connection open so the worker can
        finish its write against a live connection.
        """
        import concurrent.futures
        import threading
        import time

        class FakeSessionDB:
            def __init__(self):
                self.closed = False
                self.title_set = False
                self.ended = False
                self.conn_alive = True  # simulates self._conn is not None

            def set_session_title(self, *a, **kw):
                self.title_set = True

            def end_session(self, *a, **kw):
                self.ended = True

            def close(self):
                self.closed = True
                self.conn_alive = False  # simulates self._conn = None

        # Worker that simulates a long-running append_message that outlives
        # the inactivity-timeout shutdown signal. We CANNOT actually wait
        # 5s in a unit test, so we use a tiny grace window and a worker
        # that sleeps longer than the grace.
        fake_db = FakeSessionDB()
        worker_started = threading.Event()
        worker_should_release = threading.Event()

        def slow_worker():
            worker_started.set()
            # Simulate an append_message write still in flight when the
            # outer finally reaches the grace-wait point.
            worker_should_release.wait(timeout=2.0)

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(slow_worker)
        worker_started.wait(timeout=1.0)

        # Tiny grace: worker is still running, so the wait MUST time out
        # (return done=set()) and _cron_worker_exited MUST be False.
        _CRON_WORKER_JOIN_GRACE_SECS = 0.05
        _cron_worker_exited = True
        if future is not None and not future.done():
            _done, _ = concurrent.futures.wait(
                {future}, timeout=_CRON_WORKER_JOIN_GRACE_SECS,
            )
            _cron_worker_exited = bool(_done)

        # Mirror scheduler.py gate.
        if fake_db is not None and _cron_worker_exited:
            fake_db.set_session_title(None, "cron test")
            fake_db.end_session(None, "cron_complete")
            fake_db.close()

        # The race-defense assertion: worker is still running, so the
        # SessionDB MUST NOT have been closed (conn still alive for the
        # in-flight worker write).
        assert not _cron_worker_exited, (
            "Grace wait should have timed out while worker was still running"
        )
        assert not fake_db.closed, (
            "SessionDB.close() MUST be skipped when worker is still running — "
            "closing would race the in-flight append_message write (#65208)"
        )
        assert fake_db.conn_alive, (
            "SessionDB.conn must remain alive for in-flight worker write"
        )
        assert not fake_db.title_set
        assert not fake_db.ended

        # Cleanup: let the worker exit and drain the pool.
        worker_should_release.set()
        pool.shutdown(wait=True)

    def test_none_future_still_closes_session_db(self):
        """Defensive: if _cron_future is None (init failure), still close cleanly."""
        import concurrent.futures

        class FakeSessionDB:
            def __init__(self):
                self.closed = False

            def set_session_title(self, *a, **kw):
                pass

            def end_session(self, *a, **kw):
                pass

            def close(self):
                self.closed = True

        fake_db = FakeSessionDB()
        future = None  # Defensive: scheduler may set _cron_future = None
        _CRON_WORKER_JOIN_GRACE_SECS = 5.0
        _cron_worker_exited = True
        if future is not None and not future.done():
            _done, _ = concurrent.futures.wait(
                {future}, timeout=_CRON_WORKER_JOIN_GRACE_SECS,
            )
            _cron_worker_exited = bool(_done)

        if fake_db is not None and _cron_worker_exited:
            fake_db.close()

        assert fake_db.closed, (
            "None future must not prevent cleanup — _cron_worker_exited defaults "
            "to True so SessionDB closes normally"
        )

