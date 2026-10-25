import logging

from . import auth, config

logger = logging.getLogger(__name__)

# Cloudflare's bot-check cookie belongs to the TLS session that earned it and
# lasts 30 minutes, so a later process shouldn't send it.
UNSAVED_COOKIES = frozenset({"__cf_bm"})


def session_cookies(client) -> dict[str, str]:
    """cookies_to_jar loads cookies as domain-dotted session cookies, which is
    how untouched cookies are told apart from ones MFP set."""
    kept: dict[str, str] = {}
    for cookie in client.session.cookies.jar:
        if not cookie.domain.endswith("myfitnesspal.com"):
            continue
        if cookie.name in UNSAVED_COOKIES:
            continue
        untouched = (
            cookie.expires is None
            and cookie.domain.startswith(".")
            and client.loaded_cookies.get(cookie.name) == cookie.value
        )
        if untouched:
            kept.setdefault(cookie.name, cookie.value)
        elif cookie.expires is not None and not cookie.is_expired():
            kept[cookie.name] = cookie.value
    return kept


def save_session(client) -> None:
    if config.cookie_env():
        return
    cookies = session_cookies(client)
    if auth.SESSION_COOKIE not in cookies or cookies == client.saved_cookies:
        return
    auth.save_cookies(cookies)
    client.saved_cookies = cookies


def persist_session(client) -> None:
    try:
        save_session(client)
    except Exception as exc:
        # The class name only: exception text could quote a cookie.
        logger.warning(
            "Couldn't save the rotated MyFitnessPal session (%s)", type(exc).__name__
        )
