import json
import os
import sys
import tempfile

from . import config

SESSION_COOKIE = "__Secure-next-auth.session-token"
AUTH_COMMAND = "uvx mfp-mcp auth"

INSTRUCTIONS = """\
Connect your MyFitnessPal account
---------------------------------
1. Log in at https://www.myfitnesspal.com in your browser.
2. Open DevTools (F12) -> Application (Chrome) or Storage (Firefox) -> Cookies.
3. Copy the value of the '__Secure-next-auth.session-token' cookie.
   (Pasting the entire Cookie header from any request also works.)
"""


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
    saved = _read_saved().get("cookies")
    if saved:
        return saved
    return None


def saved_username() -> str | None:
    if config.username_env():
        return config.username_env()
    return _read_saved().get("username")


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
            handle.write(json.dumps(saved, indent=2))
        os.replace(temp_path, path)
    except BaseException:
        os.unlink(temp_path)
        raise


def read_cookie_paste() -> str:
    from . import paste

    if sys.stdin.isatty():
        return paste.read_hidden_line("Paste cookie (input hidden): ")
    print("(no terminal detected — reading the cookie from stdin)", file=sys.stderr)
    return sys.stdin.readline()


def _validate(cookies: dict[str, str]):
    """Builds a client from the cookies; when only the profile lookup fails (a
    known MFP issue for accounts that log in by email), asks for the username
    and retries instead of failing the whole flow."""
    from myfitnesspal.exceptions import MyfitnesspalLoginError

    from . import mfp_client

    try:
        return mfp_client.build_client(cookies)
    except MyfitnesspalLoginError as exc:
        if "profile" not in str(exc):
            raise
        if not sys.stdin.isatty():
            raise
        print(
            "\nYour cookie works, but MyFitnessPal couldn't return your profile "
            "(a known issue for some accounts)."
        )
        username = input("Your MyFitnessPal username (not email): ").strip()
        if not username:
            raise
        save_cookies(cookies, username=username)
        return mfp_client.build_client(cookies)


def session_source() -> str | None:
    if config.cookie_env():
        return "the MFP_COOKIE environment variable"
    if _read_saved().get("cookies"):
        return str(config.cookies_path())
    return None


def auto_refresh_status() -> tuple[bool, str]:
    from . import refresh

    if not refresh.available():
        return False, "off (install the [autorefresh] extra, then run auth again)"
    if not refresh.profile_seeded():
        return False, "installed, but no browser profile yet (run auth again)"
    return True, f"ready (profile at {refresh.profile_dir()})"


def run_check() -> int:
    from . import mfp_client, session_persistence

    source = session_source()
    auto_refresh_ready, auto_refresh_summary = auto_refresh_status()
    if source is None:
        print("Session:      none saved")
        print(f"Auto-refresh: {auto_refresh_summary}")
        print(f"Fix: run '{AUTH_COMMAND}' to connect your account.")
        return 1

    print(f"Session:      from {source}")
    try:
        client = mfp_client.build_client(load_cookies())
    except Exception as exc:
        print(f"Status:       rejected ({exc})")
        print(f"Auto-refresh: {auto_refresh_summary}")
        if auto_refresh_ready:
            print(
                "Fix: the server will try a headless-browser refresh on the next "
                f"tool call; if that fails, run '{AUTH_COMMAND}'."
            )
        else:
            print(f"Fix: run '{AUTH_COMMAND}' with a fresh cookie.")
        return 1

    session_persistence.persist_session(client)
    print(f"Status:       valid, connected as {client.effective_username}")
    print(f"Auto-refresh: {auto_refresh_summary}")
    if not client.session_renewed:
        print("Renewal:      MyFitnessPal didn't renew the session, so it will lapse")
        print(f"Fix: run '{AUTH_COMMAND}' with a fresh cookie.")
        return 1
    return 0


def run_auth_flow(clipboard: bool = False) -> int:
    print(INSTRUCTIONS, flush=True)
    if clipboard:
        from . import paste

        return paste.auth_from_clipboard()
    try:
        pasted = read_cookie_paste()
    except EOFError:
        pasted = ""
    if not pasted.strip():
        print(
            "No cookie received. Run this in an interactive terminal, or pipe "
            f"the token in: {AUTH_COMMAND} < token.txt",
            file=sys.stderr,
        )
        return 1
    return connect(parse_cookie_input(pasted))


def connect(cookies: dict[str, str]) -> int:
    """Validating signs in again, which supersedes the cookies given."""
    from . import mfp_client, refresh, session_persistence

    print("Validating with MyFitnessPal...")
    try:
        client = _validate(cookies)
    except Exception as exc:
        print(f"Those cookies didn't authenticate: {exc}", file=sys.stderr)
        print(
            "If this mentions Cloudflare or a 403, try MFP_IMPERSONATE=chrome124 "
            "or run from a residential IP.",
            file=sys.stderr,
        )
        return 1

    save_cookies(cookies, username=client.effective_username)
    session_persistence.persist_session(client)
    mfp_client.reset()
    print(
        f"Connected as {client.effective_username}. Cookies saved to {config.cookies_path()}"
    )

    if refresh.available():
        print("Seeding the browser profile for automatic session refresh...")
        try:
            refresh.seed_profile(client.saved_cookies)
            print(f"Auto-refresh ready (profile at {refresh.profile_dir()}).")
        except Exception as exc:
            print(
                f"Could not seed the auto-refresh browser profile: {exc}",
                file=sys.stderr,
            )
            print(
                "The server still works; sessions just need a manual re-auth when they expire."
            )
    else:
        print(
            "Optional: install with the [autorefresh] extra and run auth again to "
            "enable automatic session refresh via a headless browser."
        )
    return 0
