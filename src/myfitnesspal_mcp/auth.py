import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable

from . import config

SESSION_COOKIE = "__Secure-next-auth.session-token"
AUTH_COMMAND = "uvx mfp-mcp auth"
CLIPBOARD_POLL_SECONDS = 0.5
CLIPBOARD_TIMEOUT_SECONDS = 300
# NextAuth's session token is a ~1.6k-character JWE: base64url parts joined by dots.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9._-]{100,}")
_RETURN_OR_CTRL_D = b"\r\n\x04"
_BACKSPACE_OR_DELETE = b"\x08\x7f"

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
    """Several server processes save rotated sessions here, so the file is
    replaced in one step and a process loading it never reads half of it.
    mkstemp creates it 0600."""
    saved = _read_saved()
    saved["cookies"] = cookies
    if username:
        saved["username"] = username
    path = config.cookies_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cookies-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(saved, indent=2))
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def read_cookie_paste() -> str:
    if sys.stdin.isatty():
        return read_hidden_line("Paste cookie (input hidden): ")
    print("(no terminal detected — reading the cookie from stdin)", file=sys.stderr)
    return sys.stdin.readline()


def read_hidden_line(prompt: str, fd: int | None = None) -> str:
    """getpass reads a canonical-mode line, which macOS caps at 1024 bytes
    (MAX_CANON). The session token is ~1,600 characters, so a paste was cut
    short, the Enter after it was dropped, and the prompt hung. Reading with
    canonical mode off has no cap."""
    try:
        import termios
    except ImportError:  # Windows: getpass reads key by key, with no line cap
        return getpass.getpass(prompt)
    fd = sys.stdin.fileno() if fd is None else fd
    restore = termios.tcgetattr(fd)
    raw = termios.tcgetattr(fd)
    raw[3] &= ~(termios.ECHO | termios.ICANON)
    raw[6][termios.VMIN], raw[6][termios.VTIME] = 1, 0
    print(prompt, end="", file=sys.stderr, flush=True)
    typed = bytearray()
    termios.tcsetattr(fd, termios.TCSANOW, raw)
    try:
        while chunk := os.read(fd, 4096):
            for byte in chunk:
                if byte in _RETURN_OR_CTRL_D:
                    return typed.decode(errors="replace")
                if byte in _BACKSPACE_OR_DELETE:
                    del typed[-1:]
                else:
                    typed.append(byte)
        return typed.decode(errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, restore)
        print(file=sys.stderr)


def read_clipboard() -> str:
    return subprocess.run(
        ["pbpaste"], capture_output=True, text=True, check=True
    ).stdout


def clipboard_session(text: str) -> dict[str, str] | None:
    """None unless `text` holds a session token, so whatever was copied
    before (often the auth command itself) is never sent to MyFitnessPal."""
    cookies = parse_cookie_input(text)
    if _TOKEN_SHAPE.fullmatch(cookies.get(SESSION_COOKIE, "")):
        return cookies
    return None


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
    from . import mfp_client

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
        # Building the client renewed the session and may have rotated it; a
        # saved copy left behind is what stops working.
        mfp_client.persist_session(client)
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

    print(f"Status:       valid, connected as {client.effective_username}")
    print(f"Auto-refresh: {auto_refresh_summary}")
    if not getattr(client, "session_renewed", True):
        print("Renewal:      MyFitnessPal didn't renew the session, so it will lapse")
        print(f"Fix: run '{AUTH_COMMAND}' with a fresh cookie.")
        return 1
    return 0


def run_auth_flow(clipboard: bool = False) -> int:
    print(INSTRUCTIONS, flush=True)
    if clipboard:
        return auth_from_clipboard()
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


def auth_from_clipboard(
    read: Callable[[], str] = read_clipboard,
    timeout: float = CLIPBOARD_TIMEOUT_SECONDS,
) -> int:
    """The cookie never passes through the terminal, so its line cap and a
    clipboard that still holds the command can't get in the way."""
    print("Waiting for the cookie on your clipboard (Ctrl-C to stop)...", flush=True)
    tried: set[str] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            cookies = clipboard_session(read())
        except (OSError, subprocess.CalledProcessError) as exc:
            print(
                f"Can't read the clipboard ({exc}); run auth without "
                "--clipboard and paste the cookie instead.",
                file=sys.stderr,
            )
            return 1
        if cookies and cookies[SESSION_COOKIE] not in tried:
            tried.add(cookies[SESSION_COOKIE])
            if connect(cookies) == 0:
                return 0
            print("Waiting for a different cookie on the clipboard...", flush=True)
        time.sleep(CLIPBOARD_POLL_SECONDS)
    print(
        f"No working cookie reached the clipboard within {timeout / 60:.0f} minutes.",
        file=sys.stderr,
    )
    return 1


def connect(cookies: dict[str, str]) -> int:
    """Saves the session MyFitnessPal returns, not the cookies given:
    validating signs in again, which supersedes them."""
    from . import mfp_client, refresh

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

    session = mfp_client.session_cookies(client, cookies)
    if SESSION_COOKIE not in session:
        session = cookies
    save_cookies(session, username=client.effective_username)
    mfp_client.reset()
    print(
        f"Connected as {client.effective_username}. Cookies saved to {config.cookies_path()}"
    )

    if refresh.available():
        print("Seeding the browser profile for automatic session refresh...")
        try:
            refresh.seed_profile(session)
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
