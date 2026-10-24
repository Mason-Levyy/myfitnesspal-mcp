import datetime
import json
import stat
import time

import pytest

from myfitnesspal_mcp import mfp_client

SESSION = mfp_client.auth.SESSION_COOKIE
LATER = int(time.time()) + 30 * 86400


def test_cookies_to_jar_scopes_to_myfitnesspal():
    jar = mfp_client.cookies_to_jar({"a": "1", "b": "2"})
    cookies = list(jar)
    assert {c.name for c in cookies} == {"a", "b"}
    assert all(c.domain == ".myfitnesspal.com" and c.secure for c in cookies)


def test_username_override_used_when_profile_fails(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, jar, username=None, impersonate=None):
            self._username_override = username
            captured["impersonate"] = impersonate

        def _get_user_metadata(self):
            return mfp_client.CurlCffiClient._get_user_metadata(self)

        def _get_auth_data(self):
            return {}

    def boom(self):
        raise RuntimeError("status 500")

    monkeypatch.setattr(mfp_client.myfitnesspal.Client, "_get_user_metadata", boom)

    fake = FakeClient(None, username="injected-name", impersonate="chrome124")
    assert fake._get_user_metadata() == {"username": "injected-name"}
    assert captured["impersonate"] == "chrome124"


def test_own_diary_date_url_drops_username():
    client = object.__new__(mfp_client.CurlCffiClient)
    url = client._get_url_for_date(datetime.date(2026, 9, 29), "tester")
    assert url == "https://www.myfitnesspal.com/food/diary?date=2026-09-29"


def test_friend_diary_date_url_keeps_friend_username():
    client = object.__new__(mfp_client.CurlCffiClient)
    url = client._get_url_for_date(datetime.date(2026, 9, 29), "tester", "buddy")
    assert url == "https://www.myfitnesspal.com/food/diary/buddy?date=2026-09-29"


def test_is_auth_error_matches_expired_language():
    assert mfp_client.is_auth_error(RuntimeError("401 Unauthorized"))
    assert not mfp_client.is_auth_error(RuntimeError("no food found"))


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
    assert mfp_client.session_cookies(client, client.loaded_cookies) == {
        SESSION: "rotated",
        "remember_me": "renewed",
    }


def test_session_cookies_keeps_loaded_cookies_mfp_left_alone(jar_client):
    client = jar_client({SESSION: "pasted", "p": "device"})
    assert mfp_client.session_cookies(client, client.loaded_cookies) == {
        SESSION: "pasted",
        "p": "device",
    }


@pytest.fixture
def cookies_file(tmp_path, monkeypatch):
    path = tmp_path / "cookies.json"
    monkeypatch.setattr(mfp_client.config, "cookies_path", lambda: path)
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    return path


def test_save_session_writes_a_rotation_once(cookies_file, jar_client):
    client = jar_client(
        {SESSION: "pasted"}, [(SESSION, "rotated", "www.myfitnesspal.com", LATER)]
    )

    mfp_client.save_session(client)
    assert json.loads(cookies_file.read_text())["cookies"] == {SESSION: "rotated"}
    assert stat.S_IMODE(cookies_file.stat().st_mode) == 0o600

    cookies_file.unlink()
    mfp_client.save_session(client)
    assert not cookies_file.exists()


def test_renew_session_reports_whether_nextauth_renewed(client, make_response):
    client.session.route(
        "GET",
        "api/auth/session",
        make_response(json_data={"expires": "2026-11-23T22:46:52.744Z"}),
    )
    assert mfp_client.renew_session(client) is True

    client.session.route("GET", "api/auth/session", make_response(json_data={}))
    assert mfp_client.renew_session(client) is False


def test_save_session_leaves_the_file_alone_for_env_cookies(
    cookies_file, jar_client, monkeypatch
):
    monkeypatch.setenv("MFP_COOKIE", "envtoken")
    client = jar_client(
        {SESSION: "envtoken"}, [(SESSION, "rotated", "www.myfitnesspal.com", LATER)]
    )
    mfp_client.save_session(client)
    assert not cookies_file.exists()
