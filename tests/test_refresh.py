import threading

import pytest

from myfitnesspal_mcp import auth, refresh

WAIT_SECONDS = 5


class ObservedLock:
    """A lock that signals each time a caller starts waiting to acquire it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.acquire_attempts = 0
        self.second_caller_waiting = threading.Event()

    def __enter__(self):
        self.acquire_attempts += 1
        if self.acquire_attempts == 2:
            self.second_caller_waiting.set()
        self._lock.acquire()
        return self

    def __exit__(self, *exc_info):
        self._lock.release()


@pytest.fixture
def seeded_browser(monkeypatch):
    monkeypatch.setattr(refresh, "available", lambda: True)
    monkeypatch.setattr(refresh, "profile_seeded", lambda: True)
    saved = []
    monkeypatch.setattr(auth, "save_cookies", saved.append)
    return saved


def test_refresh_saves_harvested_session(seeded_browser, monkeypatch):
    harvested = {auth.SESSION_COOKIE: "fresh"}
    monkeypatch.setattr(refresh, "_visit_and_harvest", lambda seed: harvested)
    refresh.refresh_session()
    assert seeded_browser == [harvested]


def test_concurrent_refreshes_launch_one_browser(seeded_browser, monkeypatch):
    observed_lock = ObservedLock()
    monkeypatch.setattr(refresh, "_refresh_lock", observed_lock)
    browser_running = threading.Event()
    let_browser_finish = threading.Event()
    browser_launches = []

    def slow_harvest(seed):
        browser_launches.append(seed)
        browser_running.set()
        let_browser_finish.wait(WAIT_SECONDS)
        return {auth.SESSION_COOKIE: "fresh"}

    monkeypatch.setattr(refresh, "_visit_and_harvest", slow_harvest)

    first = threading.Thread(target=refresh.refresh_session)
    first.start()
    assert browser_running.wait(WAIT_SECONDS)
    second = threading.Thread(target=refresh.refresh_session)
    second.start()
    assert observed_lock.second_caller_waiting.wait(WAIT_SECONDS)
    let_browser_finish.set()
    first.join(WAIT_SECONDS)
    second.join(WAIT_SECONDS)

    assert len(browser_launches) == 1
    assert len(seeded_browser) == 1


def test_later_refresh_runs_again(seeded_browser, monkeypatch):
    """A refresh requested after the previous one finished reflects a new
    failure, so it must launch the browser rather than reuse the old result."""
    browser_launches = []

    def harvest(seed):
        browser_launches.append(seed)
        return {auth.SESSION_COOKIE: "fresh"}

    monkeypatch.setattr(refresh, "_visit_and_harvest", harvest)
    refresh.refresh_session()
    refresh.refresh_session()
    assert len(browser_launches) == 2


def test_failed_refresh_does_not_block_later_ones(seeded_browser, monkeypatch):
    def broken_harvest(seed):
        raise RuntimeError("page.goto timed out")

    monkeypatch.setattr(refresh, "_visit_and_harvest", broken_harvest)
    with pytest.raises(RuntimeError):
        refresh.refresh_session()

    harvested = {auth.SESSION_COOKIE: "fresh"}
    monkeypatch.setattr(refresh, "_visit_and_harvest", lambda seed: harvested)
    refresh.refresh_session()
    assert seeded_browser == [harvested]
