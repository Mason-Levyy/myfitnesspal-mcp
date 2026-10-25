import json
import stat
import time

import pytest

from myfitnesspal_mcp import auth, session_persistence

SESSION = auth.SESSION_COOKIE
LATER = int(time.time()) + 30 * 86400


def test_session_cookies_keeps_what_a_browser_keeps_across_a_restart(jar_client):
    client = jar_client(
        {SESSION: "pasted", "remember_me": "old"},
        [
            (SESSION, "rotated", "www.myfitnesspal.com", LATER),
            ("remember_me", "renewed", "www.myfitnesspal.com", LATER),
            ("_mfp_session", "rails", "www.myfitnesspal.com", None),
            ("__cf_bm", "bot-check", ".myfitnesspal.com", LATER),
            ("refresh-token-data", "deleted", "www.myfitnesspal.com", 1),
            ("elsewhere", "x", ".example.com", LATER),
        ],
    )
    assert session_persistence.session_cookies(client) == {
        SESSION: "rotated",
        "remember_me": "renewed",
    }


def test_session_cookies_keeps_loaded_cookies_mfp_left_alone(jar_client):
    client = jar_client({SESSION: "pasted", "p": "device"})
    assert session_persistence.session_cookies(client) == {
        SESSION: "pasted",
        "p": "device",
    }


@pytest.fixture
def cookies_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    monkeypatch.setattr(session_persistence.config, "cookies_path", lambda: path)
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    return path


def test_save_session_writes_a_rotation_once(cookies_file, jar_client):
    client = jar_client(
        {SESSION: "pasted"}, [(SESSION, "rotated", "www.myfitnesspal.com", LATER)]
    )

    session_persistence.save_session(client)
    assert json.loads(cookies_file.read_text())["cookies"] == {SESSION: "rotated"}
    assert stat.S_IMODE(cookies_file.stat().st_mode) == 0o600

    cookies_file.unlink()
    session_persistence.save_session(client)
    assert not cookies_file.exists()


def test_save_session_leaves_the_file_alone_for_env_cookies(
    cookies_file, jar_client, monkeypatch
):
    monkeypatch.setenv("MFP_COOKIE", "envtoken")
    client = jar_client(
        {SESSION: "envtoken"}, [(SESSION, "rotated", "www.myfitnesspal.com", LATER)]
    )
    session_persistence.save_session(client)
    assert not cookies_file.exists()


def test_persist_session_logs_no_error_text(
    cookies_file, jar_client, monkeypatch, caplog
):
    client = jar_client(
        {SESSION: "pasted"}, [(SESSION, "secret-value", "www.myfitnesspal.com", LATER)]
    )

    def fail(cookies, username=None):
        raise OSError(f"couldn't write {cookies[SESSION]}")

    monkeypatch.setattr(session_persistence.auth, "save_cookies", fail)
    session_persistence.persist_session(client)
    assert "OSError" in caplog.text
    assert "secret-value" not in caplog.text
