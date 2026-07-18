import importlib
import os
import sys

from hermes_cli.env_loader import load_hermes_dotenv


def test_user_env_overrides_stale_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    env_file.write_text("OPENAI_BASE_URL=https://new.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"


def test_project_env_overrides_stale_shell_values_when_user_env_missing(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://project.example/v1"


def test_project_env_is_sanitized_before_loading(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    project_env = tmp_path / ".env"
    project_env.write_text(
        "TELEGRAM_BOT_TOKEN=0123456789:test"
        "ANTHROPIC_API_KEY=sk-ant-test123\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [project_env]
    assert os.getenv("TELEGRAM_BOT_TOKEN") == "0123456789:test"
    assert os.getenv("ANTHROPIC_API_KEY") == "sk-ant-test123"


def test_user_env_takes_precedence_over_project_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    user_env = home / ".env"
    project_env = tmp_path / ".env"
    user_env.write_text("OPENAI_BASE_URL=https://user.example/v1\n", encoding="utf-8")
    project_env.write_text("OPENAI_BASE_URL=https://project.example/v1\nOPENAI_API_KEY=project-key\n", encoding="utf-8")

    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home, project_env=project_env)

    assert loaded == [user_env, project_env]
    assert os.getenv("OPENAI_BASE_URL") == "https://user.example/v1"
    assert os.getenv("OPENAI_API_KEY") == "project-key"


def test_null_bytes_in_user_env_are_stripped(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    # Null bytes can be introduced when copy-pasting API keys.
    env_file.write_text("GLM_API_KEY=abc\x00\x00\nOPENAI_API_KEY=sk-123\n", encoding="utf-8")

    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    loaded = load_hermes_dotenv(hermes_home=home)

    assert loaded == [env_file]
    assert os.getenv("GLM_API_KEY") == "abc"
    assert os.getenv("OPENAI_API_KEY") == "sk-123"


def test_main_import_applies_user_env_over_shell_values(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / ".env").write_text(
        "OPENAI_BASE_URL=https://new.example/v1\nHERMES_INFERENCE_PROVIDER=custom\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("OPENAI_BASE_URL", "https://old.example/v1")
    monkeypatch.setenv("HERMES_INFERENCE_PROVIDER", "openrouter")

    sys.modules.pop("hermes_cli.main", None)
    importlib.import_module("hermes_cli.main")

    assert os.getenv("OPENAI_BASE_URL") == "https://new.example/v1"
    assert os.getenv("HERMES_INFERENCE_PROVIDER") == "custom"


import codecs


def test_utf16_le_bom_env_is_transcoded(tmp_path, monkeypatch):
    """Regression for #66474: UTF-16 LE + BOM must not corrupt the first key.

    Windows Notepad "Unicode" save produces UTF-16-LE + BOM.  Before the fix,
    the sanitizer opened the file as utf-8-sig with errors=replace, which
    turned the FF FE BOM into U+FFFD U+FFFD glued onto the first key name,
    then rewrote the file with the mangled bytes — permanently dropping the
    key from `os.environ` even after a later UTF-8 editor re-save.
    """
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    content = "HERMES_TEST_KEY_UTF16=hello_utf16\nSECOND_KEY=world\n"
    env_file.write_bytes(codecs.BOM_UTF16_LE + content.encode("utf-16-le"))

    monkeypatch.delenv("HERMES_TEST_KEY_UTF16", raising=False)
    monkeypatch.delenv("SECOND_KEY", raising=False)

    load_hermes_dotenv(hermes_home=home)

    assert os.getenv("HERMES_TEST_KEY_UTF16") == "hello_utf16"
    assert os.getenv("SECOND_KEY") == "world"
    # No mangled-name leak into os.environ
    assert os.getenv("\ufffd\ufffdHERMES_TEST_KEY_UTF16") is None
    # On-disk file is now valid UTF-8 with the original key intact
    on_disk = env_file.read_text(encoding="utf-8-sig")
    assert "HERMES_TEST_KEY_UTF16=hello_utf16" in on_disk
    assert "SECOND_KEY=world" in on_disk


def test_utf16_be_bom_env_is_transcoded(tmp_path, monkeypatch):
    """Regression for #66474: UTF-16 BE + BOM likewise transcoded."""
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    content = "BE_KEY=value_be\nOTHER=ok\n"
    env_file.write_bytes(codecs.BOM_UTF16_BE + content.encode("utf-16-be"))

    monkeypatch.delenv("BE_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)

    load_hermes_dotenv(hermes_home=home)

    assert os.getenv("BE_KEY") == "value_be"
    assert os.getenv("OTHER") == "ok"
    on_disk = env_file.read_text(encoding="utf-8-sig")
    assert "BE_KEY=value_be" in on_disk


def test_utf8_file_unchanged_when_no_bom(tmp_path, monkeypatch):
    """Sanity guard: a plain UTF-8 file must not be rewritten just for BOM detection."""
    home = tmp_path / "hermes"
    home.mkdir()
    env_file = home / ".env"
    content = "PLAIN_KEY=plain_value\n"
    env_file.write_text(content, encoding="utf-8")
    mtime_before = env_file.stat().st_mtime_ns

    monkeypatch.delenv("PLAIN_KEY", raising=False)
    load_hermes_dotenv(hermes_home=home)

    assert os.getenv("PLAIN_KEY") == "plain_value"
    # File content unchanged
    assert env_file.read_text(encoding="utf-8") == content
