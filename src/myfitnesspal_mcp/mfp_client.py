import logging
import re
import uuid
from http.cookiejar import Cookie, CookieJar

import myfitnesspal
from curl_cffi import requests as cffi_requests
from myfitnesspal.exceptions import MyfitnesspalLoginError

from . import auth, config, diary, entry_matching

logger = logging.getLogger(__name__)

RECONNECT_HINT = (
    "MyFitnessPal session expired or not connected. "
    f"Re-run '{auth.AUTH_COMMAND}' or update MFP_COOKIE, then retry."
)

# Cloudflare's bot-check cookie belongs to the TLS session that earned it and
# lasts 30 minutes, so a later process shouldn't send it.
UNSAVED_COOKIES = frozenset({"__cf_bm"})
SESSION_URL = "https://www.myfitnesspal.com/api/auth/session"


class NotConnectedError(RuntimeError):
    pass


_AUTH_ERROR_PATTERN = re.compile(
    r"\b(401|403|unauthorized|forbidden|csrf|login|log in|logged in|session|token)\b",
    re.IGNORECASE,
)


def is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, (NotConnectedError, MyfitnesspalLoginError)):
        return True
    if isinstance(exc, diary.DiarySignedOut):
        return True
    if isinstance(exc, entry_matching.DiaryLookupError):
        return False
    return bool(_AUTH_ERROR_PATTERN.search(str(exc)))


class CurlCffiClient(myfitnesspal.Client):
    """MyFitnessPal sits behind Cloudflare, which fingerprints the upstream
    cloudscraper transport as a bot and 403s even with valid cookies. A real
    Chrome TLS/JA3 fingerprint passes with just the NextAuth session cookie.
    """

    def __init__(
        self,
        cookiejar: CookieJar,
        username: str | None = None,
        impersonate: str | None = None,
    ):
        self._username_override = username
        self._client_instance_id = uuid.uuid4()
        self._request_counter = 0
        self._log_requests_to = None
        self.unit_aware = False
        self.session = cffi_requests.Session(
            impersonate=impersonate or config.impersonate()
        )
        self.session.cookies.update(cookiejar)
        self._auth_data = self._get_auth_data()
        self._user_metadata = self._get_user_metadata()

    def _get_user_metadata(self):
        """MFP's v2 users endpoint 500s for some accounts; fall back to the
        configured username, which is all the diary URLs need."""
        try:
            meta = super()._get_user_metadata()
            if meta and meta.get("username"):
                return meta
        except Exception as exc:
            logger.debug("profile lookup failed, using the saved username: %s", exc)
        username = self._username_override or auth.saved_username()
        if not username:
            raise MyfitnesspalLoginError(
                "Authenticated, but couldn't read your MyFitnessPal profile. "
                "Set MFP_USERNAME to your MyFitnessPal username (not email) and retry."
            )
        return {"username": username}

    def _get_url_for_date(self, date, username, friend_username=None) -> str:
        if friend_username is not None:
            return super()._get_url_for_date(date, username, friend_username)
        return diary.food_diary_url(self, date)


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
    client.loaded_cookies = client.saved_cookies = dict(cookies)
    client.session_renewed = renew_session(client)
    return client


def renew_session(client) -> bool:
    """Reading the NextAuth session re-issues its cookie, good for another 30
    days, as a browser does on every page load. Signing in through
    remember_me never re-issues it, so without this even a session in daily
    use lapses 30 days after it was pasted. False when MFP didn't renew it."""
    try:
        resp = client.session.get(SESSION_URL, headers={"Accept": "application/json"})
        return bool(resp.json().get("expires"))
    except Exception:
        logger.warning("Couldn't renew the MyFitnessPal session", exc_info=True)
        return False


def session_cookies(client, loaded: dict[str, str]) -> dict[str, str]:
    """What a browser keeps across a restart: cookies MFP set with an expiry,
    plus loaded ones MFP left alone.

    Building a client can sign in again through /account/login, which issues
    a new session token and refresh token on every call, and renew_session
    re-issues the session token. Kept only in memory, they left the next
    process on a superseded login that MFP stopped accepting within about a
    day. cookies_to_jar loads cookies as session cookies on .myfitnesspal.com,
    which is how an untouched one is told apart from MFP's own sets."""
    kept: dict[str, str] = {}
    for cookie in client.session.cookies.jar:
        if not cookie.domain.endswith("myfitnesspal.com"):
            continue
        if cookie.name in UNSAVED_COOKIES:
            continue
        untouched = (
            cookie.expires is None
            and cookie.domain.startswith(".")
            and loaded.get(cookie.name) == cookie.value
        )
        if untouched:
            kept.setdefault(cookie.name, cookie.value)
        elif cookie.expires is not None and not cookie.is_expired():
            kept[cookie.name] = cookie.value
    return kept


def save_session(client) -> None:
    """Skipped under MFP_COOKIE, since the file isn't read then."""
    loaded = getattr(client, "loaded_cookies", None)
    if loaded is None or config.cookie_env():
        return
    cookies = session_cookies(client, loaded)
    if auth.SESSION_COOKIE not in cookies or cookies == client.saved_cookies:
        return
    auth.save_cookies(cookies)
    client.saved_cookies = cookies


def persist_session(client: CurlCffiClient | None = None) -> None:
    """Logs a failed save instead of raising, so saving never fails the tool
    call that rotated the session."""
    client = client or _client
    if client is None:
        return
    try:
        save_session(client)
    except Exception:
        logger.warning("Couldn't save the rotated MyFitnessPal session", exc_info=True)


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
    persist_session(_client)
    return _client


def reset() -> None:
    global _client
    _client = None
