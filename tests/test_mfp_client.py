import pytest

from myfitnesspal_mcp import mfp_client


def test_cookies_to_jar_scopes_to_myfitnesspal():
    jar = mfp_client.cookies_to_jar({"a": "1", "b": "2"})
    cookies = list(jar)
    assert {c.name for c in cookies} == {"a", "b"}
    assert all(c.domain == ".myfitnesspal.com" and c.secure for c in cookies)


JSON = {"Content-Type": "application/json; charset=utf-8"}


def test_auth_token_read_from_json(client, make_response):
    token = {"access_token": "abc", "user_id": "42"}
    client.session.route(
        "GET", "user/auth_token", make_response(json_data=token, headers=JSON)
    )
    assert mfp_client.fetch_auth_token(client) == token


@pytest.mark.parametrize(
    "response",
    [
        {"text": "<html>Log in</html>", "headers": {"Content-Type": "text/html"}},
        {"status_code": 503, "headers": JSON},
    ],
)
def test_auth_token_rejection_is_an_auth_error(client, make_response, response):
    client.session.route("GET", "user/auth_token", make_response(**response))
    with pytest.raises(mfp_client.NotConnectedError) as raised:
        mfp_client.fetch_auth_token(client)
    assert mfp_client.is_auth_error(raised.value)


def test_username_read_from_profile(client, make_response):
    client.session.route(
        "GET",
        "v2/users/user-1?",
        make_response(json_data={"item": {"username": "pal"}}),
    )
    assert mfp_client.resolve_username(client, "override") == "pal"
    url = client.session.calls[-1][1]
    assert url.startswith("https://api.myfitnesspal.com/v2/users/user-1?")
    assert "fields%5B%5D=account" in url


def test_username_override_used_when_profile_fails(client, make_response):
    client.session.route("GET", "v2/users/", make_response(status_code=500))
    assert mfp_client.resolve_username(client, "injected-name") == "injected-name"


def test_saved_username_used_when_profile_fails(client, make_response, monkeypatch):
    client.session.route("GET", "v2/users/", make_response(status_code=500))
    monkeypatch.setattr(mfp_client.auth, "saved_username", lambda: "saved-name")
    assert mfp_client.resolve_username(client) == "saved-name"


def test_no_username_anywhere_asks_for_one(client, make_response, monkeypatch):
    client.session.route("GET", "v2/users/", make_response(status_code=500))
    monkeypatch.setattr(mfp_client.auth, "saved_username", lambda: None)
    with pytest.raises(mfp_client.ProfileUnavailable, match="MFP_USERNAME"):
        mfp_client.resolve_username(client)


def test_is_auth_error_matches_expired_language():
    assert mfp_client.is_auth_error(RuntimeError("401 Unauthorized"))
    assert not mfp_client.is_auth_error(RuntimeError("no food found"))


def test_renew_session_reports_whether_nextauth_renewed(client, make_response):
    client.session.route(
        "GET",
        "api/auth/session",
        make_response(json_data={"expires": "2026-11-23T22:46:52.744Z"}),
    )
    assert mfp_client.renew_session(client) is True

    client.session.route("GET", "api/auth/session", make_response(json_data={}))
    assert mfp_client.renew_session(client) is False


def test_renew_session_logs_no_error_text(client, caplog):
    def leak(url, **kwargs):
        raise RuntimeError("Cookie: __Secure-next-auth.session-token=secret")

    client.session.get = leak
    assert mfp_client.renew_session(client) is False
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text
