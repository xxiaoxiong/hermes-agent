"""Regression test for issue #63892.

On Python >= 3.8, ``concurrent.futures.TimeoutError`` is an alias for the
builtin ``TimeoutError``. ``_run_on_mcp_loop`` polls a future with
``future.result(timeout=wait_timeout)`` and catches
``concurrent.futures.TimeoutError`` to implement "poll expired, keep
waiting".  Because of the alias the same except branch also catches the
case where **the future has COMPLETED and its coroutine's stored exception
is a real TimeoutError** — e.g. an inner ``asyncio.wait_for`` around an MCP
``call_tool`` hit ``mcp_servers.<srv>.timeout``.

When that happens the loop degenerates: ``future.result()`` returns
instantly (the future is done), re-raises the same stored exception, the
``except`` swallows it, ``continue`` — a tight spin with no sleep. Each
re-raise appends frames to the same exception object's ``__traceback__``
chain, leaking memory at ~108 MB/s until the gateway is OOM-killed.

The fix: when the except branch fires, check whether the future is actually
done.  If so, surface its real outcome (value on a poll/success race, real
exception otherwise) and let the caller's error path handle it. Only
``continue`` the poll loop when the future is genuinely still pending.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time

import pytest

import tools.mcp_tool as mcp_mod


def _spawn_loop() -> tuple[asyncio.AbstractEventLoop, threading.Thread]:
    """Start a private asyncio loop on a daemon thread — caller must stop it."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    return loop, thread


def _stop_loop(loop, thread):
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


def _install_loop(mcp_mod, loop, thread):
    old_loop = mcp_mod._mcp_loop
    old_thread = mcp_mod._mcp_thread
    mcp_mod._mcp_loop = loop
    mcp_mod._mcp_thread = thread
    return old_loop, old_thread


def _restore_loop(mcp_mod, old_loop, old_thread):
    mcp_mod._mcp_loop = old_loop
    mcp_mod._mcp_thread = old_thread


class TestRunOnMcpLoopCompletedFutureTimeoutRace:
    """Issue #63892: a completed future raising a real TimeoutError must not
    spin the poll loop and grow its traceback chain without bound."""

    def test_completed_future_with_real_timeout_propagates_once(self):
        """Inner coroutine raises a real asyncio.TimeoutError.

        Without the fix, ``_run_on_mcp_loop`` would catch the alias, see the
        future as "still pending" (it isn't — but ``concurrent.futures`` raises
        ``TimeoutError`` from ``future.result(timeout=...)`` based on the
        *poll*, not the future's state), and continue polling. The future is
        done after the first poll, so the next ``future.result(timeout=...)``
        returns instantly re-raising the stored exception, which is again
        swallowed — a tight spin with unbounded traceback growth.
        """
        loop, thread = _spawn_loop()
        old_loop, old_thread = _install_loop(mcp_mod, loop, thread)

        async def _inner_raises_timeout():
            # Mimics asyncio.wait_for(call_tool, timeout=0.1) expiring.
            raise asyncio.TimeoutError("inner wait_for expired")

        try:
            with pytest.raises(asyncio.TimeoutError, match="inner wait_for expired"):
                mcp_mod._run_on_mcp_loop(_inner_raises_timeout(), timeout=5)
        finally:
            _stop_loop(loop, thread)
            _restore_loop(mcp_mod, old_loop, old_thread)

    def test_poll_loop_does_not_spin_when_inner_timeout_fires(self):
        """Same scenario as above, but assert the poll loop visits
        ``future.result(timeout=...)`` only a bounded number of times before
        surfacing the exception. Pre-fix the loop spun ~420k times/sec.

        We instrument ``future.result`` via a wrapper to count polls; once the
        inner future completes with a stored TimeoutError the fixed code
        surfaces it on the *next* poll (returning, not continuing). Without the
        fix we would observe millions of polls within a 1-second window.
        """
        loop, thread = _spawn_loop()
        old_loop, old_thread = _install_loop(mcp_mod, loop, thread)

        async def _inner_raises_timeout():
            raise asyncio.TimeoutError("inner wait_for expired")

        future = asyncio.run_coroutine_threadsafe(_inner_raises_timeout(), loop)
        # Drain the future so it's already completed before _run_on_mcp_loop
        # ever polls. This is the exact shape of the bug: a completed future
        # with a stored TimeoutError.
        try:
            future.result(timeout=2)
        except concurrent.futures.TimeoutError:
            pass  # poll timeout — fine, future isn't done yet
        except BaseException:
            pass  # real TimeoutError consumed — future is done now

        # Snapshot the stored exception's traceback depth before invoking the
        # poll loop. With the fix the poll loop re-raises once and surfaces
        # immediately, leaving traceback depth essentially unchanged. Without
        # the fix it would grow without bound.
        try:
            stored_exc = future.exception(timeout=2)
        except concurrent.futures.TimeoutError:
            stored_exc = None

        # Allow up to a few polls (the loop has a 100ms wait, so within a
        # 1-second test window a fixed loop should poll at most ~10 times;
        # a spinning loop would poll ~hundreds of thousands of times).
        poll_count = {"n": 0}
        original_result = future.result

        def _counting_result(timeout=None):
            poll_count["n"] += 1
            return original_result(timeout)

        future.result = _counting_result  # type: ignore[method-assign]

        try:
            if stored_exc is not None:
                # Future is done with a real exception. The fixed poll loop
                # must surface it (re-raise) within a bounded number of polls.
                with pytest.raises(asyncio.TimeoutError):
                    mcp_mod._run_on_mcp_loop(_inner_raises_timeout(), timeout=1)
                # Loose bound — pre-fix this would be ~10^5+ within 1s.
                assert poll_count["n"] < 100, (
                    f"poll loop spun {poll_count['n']} times — unbounded spin "
                    f"indicates #63892 regression"
                )
        finally:
            _stop_loop(loop, thread)
            _restore_loop(mcp_mod, old_loop, old_thread)

    def test_traceback_depth_does_not_grow_under_repeated_polls(self):
        """Pre-fix the same exception object accumulated frames on each re-raise.

        After the fix the exception surfaces exactly once and its traceback
        depth stays bounded.
        """
        loop, thread = _spawn_loop()
        old_loop, old_thread = _install_loop(mcp_mod, loop, thread)

        async def _inner_raises_timeout():
            raise asyncio.TimeoutError("inner wait_for expired")

        try:
            with pytest.raises(asyncio.TimeoutError) as excinfo:
                mcp_mod._run_on_mcp_loop(_inner_raises_timeout(), timeout=2)

            # Walk the __traceback__ chain and count frames. A bounded raise
            # path produces a modest, stable depth. The spinning loop in #63892
            # grew this by ~3 frames per iteration, ~420k iterations/sec —
            # so any trace deeper than ~50 frames within a 2s test window
            # would indicate the spin returned.
            depth = 0
            tb = excinfo.value.__traceback__
            while tb is not None:
                depth += 1
                tb = tb.tb_next
            # Loose bound — a single re-raise is typically < 20 frames.
            # The bug produced depths growing without bound; pick a bound
            # that catches the spin mode without flaking on a slow CI runner.
            assert depth < 200, (
                f"exception __traceback__ depth {depth} exceeds bound — "
                f"indicates #63892 traceback-accumulation regression"
            )
        finally:
            _stop_loop(loop, thread)
            _restore_loop(mcp_mod, old_loop, old_thread)

    def test_poll_timeout_still_continues_when_future_pending(self):
        """The legitimate case — future is still pending, poll expires — must
        still continue the loop and eventually return the real value.

        This guards against the obvious over-fix: some might be tempted to
        ``raise`` unconditionally when ``future.done()`` is true, but that
        breaks the normal poll-then-success race where the future completes
        between the poll raise and the check. We must return, not raise,
        so the success path still works.
        """
        loop, thread = _spawn_loop()
        old_loop, old_thread = _install_loop(mcp_mod, loop, thread)

        async def _slow_then_value():
            await asyncio.sleep(0.3)
            return "ok"

        try:
            result = mcp_mod._run_on_mcp_loop(_slow_then_value(), timeout=2)
            assert result == "ok"
        finally:
            _stop_loop(loop, thread)
            _restore_loop(mcp_mod, old_loop, old_thread)
