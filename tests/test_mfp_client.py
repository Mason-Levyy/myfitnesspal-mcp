import datetime

from myfitnesspal_mcp import mfp_client


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
