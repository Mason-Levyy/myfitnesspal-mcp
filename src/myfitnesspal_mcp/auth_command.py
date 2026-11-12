import sys

from . import auth, config, mfp_client, paste, refresh, session_persistence

INSTRUCTIONS = """\
Connect your MyFitnessPal account
---------------------------------
1. Log in at https://www.myfitnesspal.com in your browser.
2. Open DevTools (F12) -> Application (Chrome) or Storage (Firefox) -> Cookies.
3. Copy the value of the '__Secure-next-auth.session-token' cookie.
   (Pasting the entire Cookie header from any request also works.)
"""


def read_cookie_paste() -> str:
    if sys.stdin.isatty():
        return paste.read_hidden_line("Paste cookie (input hidden): ")
    print("(no terminal detected — reading the cookie from stdin)", file=sys.stderr)
    return sys.stdin.readline()


def _validate(cookies: dict[str, str]) -> mfp_client.CurlCffiClient:
    try:
        return mfp_client.CurlCffiClient(cookies)
    except mfp_client.ProfileUnavailable:
        # A known MFP issue for accounts that log in by email; the cookie itself
        # is fine, so ask for the username instead of failing the whole flow.
        if not sys.stdin.isatty():
            raise
        print(
            "\nYour cookie works, but MyFitnessPal couldn't return your profile "
            "(a known issue for some accounts)."
        )
        username = input("Your MyFitnessPal username (not email): ").strip()
        if not username:
            raise
        auth.save_cookies(cookies, username=username)
        return mfp_client.CurlCffiClient(cookies)


def auto_refresh_status() -> tuple[bool, str]:
    if not refresh.available():
        return False, "off (install the [autorefresh] extra, then run auth again)"
    if not refresh.profile_seeded():
        return False, "installed, but no browser profile yet (run auth again)"
    return True, f"ready (profile at {refresh.profile_dir()})"


def run_check() -> int:
    source = auth.session_source()
    auto_refresh_ready, auto_refresh_summary = auto_refresh_status()
    if source is None:
        print("Session:      none saved")
        print(f"Auto-refresh: {auto_refresh_summary}")
        print(f"Fix: run '{auth.AUTH_COMMAND}' to connect your account.")
        return 1

    print(f"Session:      from {source}")
    try:
        client = mfp_client.CurlCffiClient(auth.load_cookies())
    except Exception as exc:
        print(f"Status:       rejected ({exc})")
        print(f"Auto-refresh: {auto_refresh_summary}")
        if auto_refresh_ready:
            print(
                "Fix: the server will try a headless-browser refresh on the next "
                f"tool call; if that fails, run '{auth.AUTH_COMMAND}'."
            )
        else:
            print(f"Fix: run '{auth.AUTH_COMMAND}' with a fresh cookie.")
        return 1

    session_persistence.persist_session(client)
    print(f"Status:       valid, connected as {client.effective_username}")
    print(f"Auto-refresh: {auto_refresh_summary}")
    if not client.session_renewed:
        print("Renewal:      MyFitnessPal didn't renew the session, so it will lapse")
        print(f"Fix: run '{auth.AUTH_COMMAND}' with a fresh cookie.")
        return 1
    return 0


def run_auth_flow(clipboard: bool = False) -> int:
    print(INSTRUCTIONS, flush=True)
    if clipboard:
        return paste.auth_from_clipboard(connect)
    try:
        pasted = read_cookie_paste()
    except EOFError:
        pasted = ""
    if not pasted.strip():
        print(
            "No cookie received. Run this in an interactive terminal, or pipe "
            f"the token in: {auth.AUTH_COMMAND} < token.txt",
            file=sys.stderr,
        )
        return 1
    return connect(auth.parse_cookie_input(pasted))


def connect(cookies: dict[str, str]) -> int:
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

    auth.save_cookies(cookies, username=client.effective_username)
    # Validating signed in again, which supersedes the cookies given.
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
