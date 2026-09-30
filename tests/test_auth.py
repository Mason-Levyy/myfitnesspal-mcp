import json
import sys

import pytest
from myfitnesspal.exceptions import MyfitnesspalLoginError

from myfitnesspal_mcp import auth, cli, mfp_client, refresh


def test_parse_bare_token():
    cookies = auth.parse_cookie_input("eyJhbGciOi.some.token")
    assert cookies == {auth.SESSION_COOKIE: "eyJhbGciOi.some.token"}


def test_parse_full_header():
    cookies = auth.parse_cookie_input(
        "Cookie: __Secure-next-auth.session-token=abc123; other=x; flagonly"
    )
    assert cookies[auth.SESSION_COOKIE] == "abc123"
    assert cookies["other"] == "x"
    assert "flagonly" not in cookies


def test_parse_single_pair():
    cookies = auth.parse_cookie_input("__Secure-next-auth.session-token=zzz")
    assert cookies == {auth.SESSION_COOKIE: "zzz"}


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "cookies_path", lambda: tmp_path / "cookies.json")
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    monkeypatch.delenv("MFP_USERNAME", raising=False)

    auth.save_cookies({"a": "1"}, username="tester")
    assert auth.load_cookies() == {"a": "1"}
    assert auth.saved_username() == "tester"

    auth.save_cookies({"a": "2"})
    saved = json.loads((tmp_path / "cookies.json").read_text())
    assert saved["cookies"] == {"a": "2"}
    assert saved["username"] == "tester"


def test_env_cookie_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "cookies_path", lambda: tmp_path / "cookies.json")
    auth.save_cookies({"file": "cookie"})
    monkeypatch.setenv("MFP_COOKIE", "envtoken")
    assert auth.load_cookies() == {auth.SESSION_COOKIE: "envtoken"}


def test_load_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "cookies_path", lambda: tmp_path / "missing.json")
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    assert auth.load_cookies() is None


class FakeConnectedClient:
    effective_username = "tester"


@pytest.fixture
def check_env(tmp_path, monkeypatch):
    cookies_file = tmp_path / "cookies.json"
    monkeypatch.setattr(auth.config, "cookies_path", lambda: cookies_file)
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    monkeypatch.delenv("MFP_USERNAME", raising=False)
    monkeypatch.setattr(refresh, "available", lambda: False)
    monkeypatch.setattr(refresh, "profile_seeded", lambda: False)
    monkeypatch.setattr(refresh, "profile_dir", lambda: tmp_path / "browser-profile")
    return cookies_file


def reject_session(cookies):
    raise MyfitnesspalLoginError("session expired")


def test_check_without_session_skips_network(check_env, monkeypatch, capsys):
    def unexpected_build(cookies):
        raise AssertionError("no session, so nothing to validate")

    monkeypatch.setattr(mfp_client, "build_client", unexpected_build)
    assert auth.run_check() == 1
    output = capsys.readouterr().out
    assert "none saved" in output
    assert "myfitnesspal-mcp auth" in output


def test_check_valid_session_changes_nothing(check_env, monkeypatch, capsys):
    auth.save_cookies({auth.SESSION_COOKIE: "token"}, username="tester")
    saved_before = check_env.read_text()
    monkeypatch.setattr(
        mfp_client, "build_client", lambda cookies: FakeConnectedClient()
    )

    assert auth.run_check() == 0
    output = capsys.readouterr().out
    assert f"from {check_env}" in output
    assert "valid, connected as tester" in output
    assert "Auto-refresh: off" in output
    assert check_env.read_text() == saved_before


def test_check_reports_env_cookie_source(check_env, monkeypatch, capsys):
    monkeypatch.setenv("MFP_COOKIE", "envtoken")
    monkeypatch.setattr(
        mfp_client, "build_client", lambda cookies: FakeConnectedClient()
    )
    assert auth.run_check() == 0
    assert "MFP_COOKIE" in capsys.readouterr().out


def test_check_rejected_session_without_auto_refresh(check_env, monkeypatch, capsys):
    auth.save_cookies({auth.SESSION_COOKIE: "stale"})
    monkeypatch.setattr(mfp_client, "build_client", reject_session)

    assert auth.run_check() == 1
    output = capsys.readouterr().out
    assert "rejected (session expired)" in output
    assert "fresh cookie" in output


def test_check_rejected_session_with_auto_refresh(check_env, monkeypatch, capsys):
    auth.save_cookies({auth.SESSION_COOKIE: "stale"})
    monkeypatch.setattr(mfp_client, "build_client", reject_session)
    monkeypatch.setattr(refresh, "available", lambda: True)
    monkeypatch.setattr(refresh, "profile_seeded", lambda: True)

    assert auth.run_check() == 1
    output = capsys.readouterr().out
    assert "Auto-refresh: ready" in output
    assert "headless-browser refresh on the next tool call" in output


def test_check_flag_requires_auth_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["myfitnesspal-mcp", "serve", "--check"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2


def test_username_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "cookies_path", lambda: tmp_path / "cookies.json")
    auth.save_cookies({"a": "1"}, username="fromfile")
    monkeypatch.setenv("MFP_USERNAME", "fromenv")
    assert auth.saved_username() == "fromenv"
