import os
import sys
import threading
import time

import pytest

from myfitnesspal_mcp import auth, paste

TOKEN = "eyJ" + "a" * 200 + ".b.c.d"


def test_clipboard_waits_past_other_text_for_a_token(monkeypatch):
    clipboard = iter(["pbpaste | uv run mfp-mcp auth", "some=thing; else=1", TOKEN])
    connected = []
    monkeypatch.setattr(auth, "connect", lambda cookies: connected.append(cookies) or 0)
    monkeypatch.setattr(paste.time, "sleep", lambda seconds: None)

    assert paste.auth_from_clipboard(read_clipboard_text=lambda: next(clipboard)) == 0
    assert connected == [{auth.SESSION_COOKIE: TOKEN}]


def test_clipboard_skips_a_rejected_token_until_a_new_one(monkeypatch):
    stale, fresh = "s" * 150, "f" * 150
    clipboard = iter([stale, stale, fresh])
    tried_tokens = []

    def fake_connect(cookies):
        tried_tokens.append(cookies[auth.SESSION_COOKIE])
        return 0 if tried_tokens[-1] == fresh else 1

    monkeypatch.setattr(auth, "connect", fake_connect)
    monkeypatch.setattr(paste.time, "sleep", lambda seconds: None)

    assert paste.auth_from_clipboard(read_clipboard_text=lambda: next(clipboard)) == 0
    assert tried_tokens == [stale, fresh]


def test_clipboard_unreadable_points_to_pasting(capsys):
    def no_pbpaste():
        raise FileNotFoundError("pbpaste")

    assert paste.auth_from_clipboard(read_clipboard_text=no_pbpaste) == 1
    assert "paste the cookie instead" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX terminal")
def test_hidden_prompt_takes_a_paste_longer_than_a_terminal_line():
    import pty
    import termios

    controller, terminal = pty.openpty()
    token = "t" * 1632  # macOS caps a canonical-mode line at 1024 bytes
    result = {}
    reader = threading.Thread(
        target=lambda: result.update(
            line=paste.read_hidden_line("Paste: ", descriptor=terminal)
        ),
        daemon=True,
    )
    try:
        reader.start()
        deadline = time.monotonic() + 5
        while termios.tcgetattr(terminal)[paste.LFLAG] & termios.ICANON:
            assert time.monotonic() < deadline, "the prompt never left canonical mode"
            time.sleep(0.01)
        pasted = (token + "\r").encode()
        while pasted:
            pasted = pasted[os.write(controller, pasted) :]
        reader.join(timeout=5)
        assert result.get("line") == token
    finally:
        os.close(controller)
        os.close(terminal)
