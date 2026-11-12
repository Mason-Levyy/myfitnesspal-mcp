import json
import os
import tempfile

from . import config

SESSION_COOKIE = "__Secure-next-auth.session-token"
AUTH_COMMAND = "uvx mfp-mcp auth"


def parse_cookie_input(text: str) -> dict[str, str]:
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()
    if "=" not in text:
        return {SESSION_COOKIE: text}
    cookies = {}
    for part in text.split(";"):
        if "=" in part:
            name, value = part.strip().split("=", 1)
            cookies[name.strip()] = value.strip()
    return cookies


def _read_saved() -> dict:
    path = config.cookies_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def load_cookies() -> dict[str, str] | None:
    env = config.cookie_env()
    if env:
        return parse_cookie_input(env)
    return _read_saved().get("cookies") or None


def saved_username() -> str | None:
    return config.username_env() or _read_saved().get("username")


def session_source() -> str | None:
    if config.cookie_env():
        return "the MFP_COOKIE environment variable"
    if _read_saved().get("cookies"):
        return str(config.cookies_path())
    return None


def save_cookies(cookies: dict[str, str], username: str | None = None) -> None:
    """mkstemp (which creates the file 0600) + replace, so concurrent
    processes never read a partial file."""
    saved = _read_saved()
    saved["cookies"] = cookies
    if username:
        saved["username"] = username
    path = config.cookies_path()
    descriptor, temp_path = tempfile.mkstemp(
        dir=path.parent, prefix=".cookies-", suffix=".json"
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(saved, handle, indent=2)
        os.replace(temp_path, path)
    except BaseException:
        os.unlink(temp_path)
        raise
