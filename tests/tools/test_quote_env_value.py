"""Tests for `_quote_env_value` / `save_env_value` (issue #66482).

Spaced paths (e.g. macOS `~/Library/Application Support/...`) must be
double-quoted on write so shell `set -a; . ~/.hermes/.env` round-trips
identically to python-dotenv.  Prior to the fix, internal whitespace
did not trigger quoting, breaking POSIX-shell sourcing.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from hermes_cli.config import _quote_env_value, save_env_value


class TestQuoteEnvValue:
    def test_simple_value_not_quoted(self):
        assert _quote_env_value("simple") == "simple"
        assert _quote_env_value("path/to/file") == "path/to/file"
        assert _quote_env_value("value_with_underscores-123") == "value_with_underscores-123"

    def test_hash_triggers_quoting(self):
        out = _quote_env_value("a#b")
        assert out.startswith('"') and out.endswith('"')

    def test_double_quote_triggers_quoting(self):
        out = _quote_env_value('a"b')
        assert out.startswith('"') and out.endswith('"')
        assert '\\"' in out, "embedded double-quote must be backslash-escaped"

    def test_single_quote_triggers_quoting(self):
        out = _quote_env_value("a'b")
        assert out.startswith('"') and out.endswith('"')

    def test_leading_trailing_whitespace_triggers_quoting(self):
        assert _quote_env_value(" leading").startswith('"')
        assert _quote_env_value("trailing ").startswith('"')

    def test_internal_space_triggers_quoting(self):
        """Regression for #66482 — spaced paths must be quoted."""
        spaced = "/Users/me/Library/Application Support/hermes/keys/id_ed25519"
        out = _quote_env_value(spaced)
        assert out != spaced, f"spaced path should be quoted, got: {out}"
        assert out.startswith('"') and out.endswith('"'), f"must be double-quoted: {out}"
        # No transformation of the inner content when no escapes are needed
        assert "Application Support" in out, "whitespace content preserved"

    def test_internal_tab_triggers_quoting(self):
        out = _quote_env_value("a\tb")
        assert out.startswith('"') and out.endswith('"')

    def test_empty_string_returned_unchanged(self):
        assert _quote_env_value("") == ""

    def test_idempotent_save(self, tmp_path, monkeypatch):
        """Re-saving the same value does not double-quote."""
        from hermes_cli.config import get_env_path

        orig_path = get_env_path()
        env_path = tmp_path / "hermes" / ".env"
        env_path.parent.mkdir(parents=True)
        monkeypatch.setattr("hermes_cli.config.get_env_path", lambda: env_path)
        save_env_value("HERMES_TEST_KEY", "/path/with spaces/to/key")
        save_env_value("HERMES_TEST_KEY", "/path/with spaces/to/key")
        text = env_path.read_text()
        lines = [ln for ln in text.splitlines() if ln.startswith("HERMES_TEST_KEY=")]
        assert len(lines) == 1, f"expected one HERMES_TEST_KEY entry, got {lines}"
        line = lines[0]
        assert line.count('"') == 2, f"expected exactly 2 double-quotes, line: {line}"
