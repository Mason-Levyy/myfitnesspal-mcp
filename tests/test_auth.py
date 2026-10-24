import json
import os
import sys
import threading
import time

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
    assert "uvx mfp-mcp auth" in output


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


def rotating_build(jar_client):
    later = int(time.time()) + 86400
    return lambda cookies: jar_client(
        cookies, [(auth.SESSION_COOKIE, "rotated", "www.myfitnesspal.com", later)]
    )


def test_check_saves_the_session_mfp_rotated(check_env, monkeypatch, jar_client):
    auth.save_cookies({auth.SESSION_COOKIE: "token"}, username="tester")
    monkeypatch.setattr(mfp_client, "build_client", rotating_build(jar_client))

    assert auth.run_check() == 0
    assert json.loads(check_env.read_text()) == {
        "cookies": {auth.SESSION_COOKIE: "rotated"},
        "username": "tester",
    }


def test_check_flags_a_session_mfp_would_not_renew(check_env, monkeypatch, capsys):
    auth.save_cookies({auth.SESSION_COOKIE: "token"})
    lapsing = FakeConnectedClient()
    lapsing.session_renewed = False
    monkeypatch.setattr(mfp_client, "build_client", lambda cookies: lapsing)

    assert auth.run_check() == 1
    assert "didn't renew" in capsys.readouterr().out


def test_connect_saves_the_session_mfp_returned_not_the_paste(
    check_env, monkeypatch, jar_client, capsys
):
    monkeypatch.setattr(mfp_client, "build_client", rotating_build(jar_client))

    assert auth.connect({auth.SESSION_COOKIE: "pasted"}) == 0
    assert json.loads(check_env.read_text()) == {
        "cookies": {auth.SESSION_COOKIE: "rotated"},
        "username": "tester",
    }
    assert "Connected as tester" in capsys.readouterr().out


TOKEN = "eyJ" + "a" * 200 + ".b.c.d"


def test_clipboard_waits_past_other_text_for_a_token(monkeypatch):
    clipboard = iter(["pbpaste | uv run mfp-mcp auth", "some=thing; else=1", TOKEN])
    connected = []
    monkeypatch.setattr(auth, "connect", lambda cookies: connected.append(cookies) or 0)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)

    assert auth.auth_from_clipboard(read=lambda: next(clipboard)) == 0
    assert connected == [{auth.SESSION_COOKIE: TOKEN}]


def test_clipboard_skips_a_rejected_token_until_a_new_one(monkeypatch):
    stale, fresh = "s" * 150, "f" * 150
    clipboard = iter([stale, stale, fresh])
    tried = []

    def fake_connect(cookies):
        tried.append(cookies[auth.SESSION_COOKIE])
        return 0 if tried[-1] == fresh else 1

    monkeypatch.setattr(auth, "connect", fake_connect)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: None)

    assert auth.auth_from_clipboard(read=lambda: next(clipboard)) == 0
    assert tried == [stale, fresh]


def test_clipboard_unreadable_points_to_pasting(capsys):
    def no_pbpaste():
        raise FileNotFoundError("pbpaste")

    assert auth.auth_from_clipboard(read=no_pbpaste) == 1
    assert "paste the cookie instead" in capsys.readouterr().err


def test_clipboard_flag_reaches_the_auth_flow(monkeypatch):
    calls = []
    monkeypatch.setattr(
        auth, "run_auth_flow", lambda clipboard=False: calls.append(clipboard) or 0
    )
    monkeypatch.setattr(sys, "argv", ["myfitnesspal-mcp", "auth", "--clipboard"])
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    assert calls == [True]


def test_clipboard_flag_needs_auth_without_check(monkeypatch):
    for args in (["serve", "--clipboard"], ["auth", "--check", "--clipboard"]):
        monkeypatch.setattr(sys, "argv", ["myfitnesspal-mcp", *args])
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        assert exit_info.value.code == 2


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX terminal")
def test_hidden_prompt_takes_a_paste_longer_than_a_terminal_line():
    import pty
    import termios

    controller, terminal = pty.openpty()
    token = "t" * 1632  # macOS caps a canonical-mode line at 1024 bytes
    result = {}
    reader = threading.Thread(
        target=lambda: result.update(
            line=auth.read_hidden_line("Paste: ", fd=terminal)
        ),
        daemon=True,
    )
    try:
        reader.start()
        deadline = time.monotonic() + 5
        while termios.tcgetattr(terminal)[3] & termios.ICANON:
            assert time.monotonic() < deadline, "the prompt never left canonical mode"
            time.sleep(0.01)
        pasted = (token + "\r").encode()
        while pasted:
            pasted = pasted[os.write(controller, pasted) :]
        reader.join(timeout=5)
        assert result.get("line") == token
    finally:
        os.close(controller)
        os.close(terminal)
