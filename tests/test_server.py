import asyncio

import pytest
from myfitnesspal.exceptions import MyfitnesspalLoginError

from myfitnesspal_mcp import mfp_client, server
from myfitnesspal_mcp.store import Store


class FakeContext:
    def __init__(self):
        self.messages = []

    async def info(self, message):
        self.messages.append(message)


def test_is_auth_error_patterns():
    assert mfp_client.is_auth_error(MyfitnesspalLoginError("bad"))
    assert mfp_client.is_auth_error(mfp_client.NotConnectedError("x"))
    assert mfp_client.is_auth_error(RuntimeError("HTTP 403 returned"))
    assert mfp_client.is_auth_error(RuntimeError("couldn't read the csrf token"))
    assert not mfp_client.is_auth_error(RuntimeError("no food found for 'kale'"))
    assert not mfp_client.is_auth_error(ValueError("bad date"))


def test_run_with_refresh_retries_auth_failures(monkeypatch):
    refreshed = []
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: refreshed.append(True))

    attempts = []

    def op():
        attempts.append(1)
        if len(attempts) == 1:
            raise MyfitnesspalLoginError("session expired")
        return "second try"

    ctx = FakeContext()
    result = asyncio.run(server.run_with_refresh(ctx, op))
    assert result == "second try"
    assert refreshed == [True]
    assert len(attempts) == 2
    assert "refreshing" in ctx.messages[0]
    assert "succeeded" in ctx.messages[1]


def test_run_with_refresh_gives_clear_error_when_retry_fails(monkeypatch):
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: None)

    def op():
        raise MyfitnesspalLoginError("still expired")

    ctx = FakeContext()
    with pytest.raises(RuntimeError, match="myfitnesspal-mcp auth"):
        asyncio.run(server.run_with_refresh(ctx, op))
    assert "Session refresh failed." in ctx.messages


def test_run_with_refresh_does_not_retry_other_errors(monkeypatch):
    def explode():
        raise AssertionError("refresh must not run")

    monkeypatch.setattr(server.refresh, "refresh_session", explode)

    attempts = []

    def op():
        attempts.append(1)
        raise ValueError("no match for that food name")

    ctx = FakeContext()
    with pytest.raises(ValueError):
        asyncio.run(server.run_with_refresh(ctx, op))
    assert len(attempts) == 1
    assert ctx.messages == []


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    test_store = Store(tmp_path / "server.db")
    monkeypatch.setattr(server, "_store", test_store)
    return test_store


def test_log_feel_tool(local_store):
    result = server.fitness_log_feel(note="strong", rating=5, date="2026-07-08")
    assert result == {"day": "2026-07-08", "note": "strong", "rating": 5}
    assert local_store.feel("2026-07-08")["rating"] == 5


def test_trends_rejects_unknown_metric(local_store):
    with pytest.raises(ValueError, match="unknown metric"):
        asyncio.run(server.fitness_get_trends(metric="steps"))


def test_bulk_export_validates_range(local_store):
    with pytest.raises(ValueError, match="start must be on or before end"):
        asyncio.run(server.fitness_bulk_export(start="2026-07-08", end="2026-07-01"))


def test_bulk_export_reads_cache_without_client(local_store):
    local_store.upsert_nutrition("2026-07-05", calories=1500.0)
    result = asyncio.run(
        server.fitness_bulk_export(start="2026-07-01", end="2026-07-08")
    )
    assert result["count"] == 1
    assert result["days"][0]["nutrition"]["calories"] == 1500.0
