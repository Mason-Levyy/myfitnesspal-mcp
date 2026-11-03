import logging
import re
from http.cookiejar import Cookie, CookieJar
from urllib import parse

from curl_cffi import requests as cffi_requests

from . import auth, config, diary, entry_matching, mfp_web, session_persistence

logger = logging.getLogger(__name__)

RECONNECT_HINT = (
    "MyFitnessPal session expired or not connected. "
    f"Re-run '{auth.AUTH_COMMAND}' or update MFP_COOKIE, then retry."
)
SESSION_URL = "https://www.myfitnesspal.com/api/auth/session"
AUTH_TOKEN_URL = "https://www.myfitnesspal.com/user/auth_token?refresh=true"
PROFILE_FIELDS = (
    "diary_preferences",
    "goal_preferences",
    "unit_preferences",
    "paid_subscriptions",
    "account",
    "goal_displays",
    "location_preferences",
    "system_data",
    "profiles",
    "step_sources",
    "privacy_preferences",
    "social_preferences",
    "app_preferences",
    "partner_only_fields",
)


class NotConnectedError(RuntimeError):
    pass


class ProfileUnavailable(NotConnectedError):
    pass


_AUTH_ERROR_PATTERN = re.compile(
    r"\b(401|403|unauthorized|forbidden|csrf|login|log in|logged in|session|token)\b",
    re.IGNORECASE,
)


def is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, (NotConnectedError, diary.DiarySignedOut)):
        return True
    if isinstance(exc, entry_matching.DiaryLookupError):
        return False
    return bool(_AUTH_ERROR_PATTERN.search(str(exc)))


class CurlCffiClient:
    """MyFitnessPal sits behind Cloudflare, which fingerprints python-requests
    style transports as a bot and 403s even with valid cookies. A real Chrome
    TLS/JA3 fingerprint passes with just the NextAuth session cookie.
    """

    BASE_URL_SECURE = "https://www.myfitnesspal.com/"
    BASE_API_URL = "https://api.myfitnesspal.com/"

    def __init__(
        self,
        cookiejar: CookieJar,
        username: str | None = None,
        impersonate: str | None = None,
    ):
        self.session = cffi_requests.Session(
            impersonate=impersonate or config.impersonate()
        )
        self.session.cookies.update(cookiejar)
        self.loaded_cookies: dict[str, str] = {}
        self.saved_cookies: dict[str, str] = {}
        self.session_renewed = True
        token = fetch_auth_token(self)
        self.access_token: str = token["access_token"]
        self.user_id: str = token["user_id"]
        self.effective_username = resolve_username(self, username)


def fetch_auth_token(client) -> dict:
    """The diary pages run on cookies, but MFP's API calls take this Bearer
    token. Anything but JSON back means the cookies didn't sign in."""
    resp = client.session.get(AUTH_TOKEN_URL)
    if resp.status_code >= 400:
        raise NotConnectedError(
            f"MyFitnessPal refused the auth token request (HTTP {resp.status_code})"
        )
    if not resp.headers.get("Content-Type", "").startswith("application/json"):
        raise NotConnectedError(
            "MyFitnessPal didn't accept the session cookie (no auth token came back)"
        )
    return resp.json()


def resolve_username(client, override: str | None = None) -> str:
    """MFP's v2 users endpoint 500s for some accounts; fall back to the
    configured username, which is all the diary URLs need."""
    query = parse.urlencode([("fields[]", field) for field in PROFILE_FIELDS])
    url = parse.urljoin(client.BASE_API_URL, f"v2/users/{client.user_id}?{query}")
    try:
        username = (mfp_web.get_json(client, url) or {})["item"].get("username")
        if username:
            return username
    except Exception as exc:
        logger.debug("profile lookup failed, using the saved username: %s", exc)
    username = override or auth.saved_username()
    if not username:
        raise ProfileUnavailable(
            "Authenticated, but couldn't read your MyFitnessPal profile. "
            "Set MFP_USERNAME to your MyFitnessPal username (not email) and retry."
        )
    return username


def cookies_to_jar(cookies: dict[str, str]) -> CookieJar:
    jar = CookieJar()
    for name, value in cookies.items():
        jar.set_cookie(
            Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=".myfitnesspal.com",
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=None,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
                rfc2109=False,
            )
        )
    return jar


def build_client(
    cookies: dict[str, str],
    username: str | None = None,
    impersonate: str | None = None,
) -> CurlCffiClient:
    client = CurlCffiClient(
        cookies_to_jar(cookies), username=username, impersonate=impersonate
    )
    client.loaded_cookies = dict(cookies)
    client.saved_cookies = dict(cookies)
    client.session_renewed = renew_session(client)
    return client


def renew_session(client) -> bool:
    """Signing in through remember_me never re-issues the session token;
    reading the NextAuth session does, good for another 30 days."""
    try:
        response = client.session.get(
            SESSION_URL, headers={"Accept": "application/json"}
        )
        return bool(response.json().get("expires"))
    except Exception as exc:
        # The class name only: exception text could quote a cookie.
        logger.warning(
            "Couldn't renew the MyFitnessPal session (%s)", type(exc).__name__
        )
        return False


_client: CurlCffiClient | None = None


def get_client() -> CurlCffiClient:
    global _client
    if _client is not None:
        return _client
    cookies = auth.load_cookies()
    if not cookies:
        raise NotConnectedError(RECONNECT_HINT)
    try:
        _client = build_client(cookies)
    except Exception as exc:
        raise NotConnectedError(f"{RECONNECT_HINT} (auth failed: {exc})") from exc
    session_persistence.persist_session(_client)
    return _client


def persist_cached_session() -> None:
    if _client is not None:
        session_persistence.persist_session(_client)


def reset() -> None:
    global _client
    _client = None
