import getpass
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable

from . import auth

CLIPBOARD_POLL_SECONDS = 0.5
CLIPBOARD_TIMEOUT_SECONDS = 300
# NextAuth's session token is a ~1.6k-character JWE: base64url parts joined by dots.
_TOKEN_SHAPE = re.compile(r"[A-Za-z0-9._-]{100,}")
_LINE_END_BYTES = b"\r\n\x04"
_ERASE_BYTES = b"\x08\x7f"
LFLAG = 3
CC = 6


def read_hidden_line(prompt: str, descriptor: int | None = None) -> str:
    """macOS caps canonical-mode lines at 1024 bytes (MAX_CANON) and the
    session token is about 1,600 characters, so getpass can't be used."""
    try:
        import termios
    except ImportError:  # Windows: getpass reads key by key, with no line cap
        return getpass.getpass(prompt)
    descriptor = sys.stdin.fileno() if descriptor is None else descriptor
    original_settings = termios.tcgetattr(descriptor)
    raw_settings = termios.tcgetattr(descriptor)
    raw_settings[LFLAG] &= ~(termios.ECHO | termios.ICANON)
    raw_settings[CC][termios.VMIN], raw_settings[CC][termios.VTIME] = 1, 0
    print(prompt, end="", file=sys.stderr, flush=True)
    typed = bytearray()
    termios.tcsetattr(descriptor, termios.TCSANOW, raw_settings)
    try:
        while chunk := os.read(descriptor, 4096):
            for byte in chunk:
                if byte in _LINE_END_BYTES:
                    return typed.decode(errors="replace")
                if byte in _ERASE_BYTES:
                    del typed[-1:]
                else:
                    typed.append(byte)
        return typed.decode(errors="replace")
    finally:
        termios.tcsetattr(descriptor, termios.TCSAFLUSH, original_settings)
        print(file=sys.stderr)


def read_clipboard() -> str:
    return subprocess.run(
        ["pbpaste"], capture_output=True, text=True, check=True
    ).stdout


def clipboard_session(text: str) -> dict[str, str] | None:
    """Whatever was copied before, often the auth command itself, is never
    sent to MyFitnessPal."""
    cookies = auth.parse_cookie_input(text)
    if _TOKEN_SHAPE.fullmatch(cookies.get(auth.SESSION_COOKIE, "")):
        return cookies
    return None


def auth_from_clipboard(
    read_clipboard_text: Callable[[], str] = read_clipboard,
    timeout: float = CLIPBOARD_TIMEOUT_SECONDS,
) -> int:
    print("Waiting for the cookie on your clipboard (Ctrl-C to stop)...", flush=True)
    tried_tokens: set[str] = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            cookies = clipboard_session(read_clipboard_text())
        except (OSError, subprocess.CalledProcessError) as exc:
            print(
                f"Can't read the clipboard ({exc}); run auth without "
                "--clipboard and paste the cookie instead.",
                file=sys.stderr,
            )
            return 1
        if cookies and cookies[auth.SESSION_COOKIE] not in tried_tokens:
            tried_tokens.add(cookies[auth.SESSION_COOKIE])
            if auth.connect(cookies) == 0:
                return 0
            print("Waiting for a different cookie on the clipboard...", flush=True)
        time.sleep(CLIPBOARD_POLL_SECONDS)
    print(
        f"No working cookie reached the clipboard within {timeout / 60:.0f} minutes.",
        file=sys.stderr,
    )
    return 1
