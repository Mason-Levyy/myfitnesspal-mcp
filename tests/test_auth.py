import json

from myfitnesspal_mcp import auth


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


def test_username_env_override(tmp_path, monkeypatch):
    monkeypatch.setattr(auth.config, "cookies_path", lambda: tmp_path / "cookies.json")
    auth.save_cookies({"a": "1"}, username="fromfile")
    monkeypatch.setenv("MFP_USERNAME", "fromenv")
    assert auth.saved_username() == "fromenv"
