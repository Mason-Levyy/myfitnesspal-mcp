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
    monkeypatch.setattr(
        server.refresh, "refresh_session", lambda: refreshed.append(True)
    )

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


def test_day_record_includes_note(local_store):
    local_store.set_note("2026-07-08", "long run, felt strong")
    assert local_store.day_record("2026-07-08")["note"] == "long run, felt strong"


def test_bulk_export_includes_note(local_store):
    local_store.set_note("2026-07-05", "rest day")
    result = asyncio.run(
        server.fitness_bulk_export(start="2026-07-01", end="2026-07-08")
    )
    assert result["count"] == 1
    assert result["days"][0]["note"] == "rest day"


def test_trends_rejects_unknown_metric(local_store):
    with pytest.raises(ValueError, match="unknown metric"):
        asyncio.run(server.fitness_get_trends(metric="steps"))


def test_bulk_export_validates_range(local_store):
    with pytest.raises(ValueError, match="start must be on or before end"):
        asyncio.run(server.fitness_bulk_export(start="2026-07-08", end="2026-07-01"))


@pytest.fixture
def connected(local_store, client, monkeypatch):
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)
    monkeypatch.setattr(server.sync, "refresh_day", lambda store, client, day: None)
    return client


def test_draft_then_log_by_option(connected, local_store):
    draft = asyncio.run(
        server.fitness_draft_food(query="banana", meal="lunch", date="2026-07-08")
    )
    result = asyncio.run(server.fitness_log_food(draft_id=draft["draft_id"], option=1))
    assert result["ok"] is True
    assert result["logged"] == "Banana"
    assert result["meal"] == "lunch"
    assert result["date"] == "2026-07-08"
    assert local_store.pin("banana")["food_id"] == "111"


def test_log_food_requires_option_with_draft(connected):
    with pytest.raises(ValueError, match="option is required"):
        asyncio.run(server.fitness_log_food(draft_id="abc"))


def test_log_food_ambiguous_query_returns_choices(connected):
    result = asyncio.run(server.fitness_log_food(query="bread"))
    assert result["ok"] is True
    assert result["needs_choice"] is True
    assert not any(call[0] == "POST" for call in connected.session.calls)


def test_log_food_with_explicit_ids(connected):
    result = asyncio.run(
        server.fitness_log_food(
            query="Banana", food_id="111", weight_id="20", date="2026-07-08"
        )
    )
    assert result["source"] == "ids"
    _, _, kwargs = connected.session.calls[-1]
    assert kwargs["data"]["food_entry[weight_id]"] == "20"


def posts_to(client, fragment):
    return [
        url
        for method, url, _ in client.session.calls
        if method == "POST" and fragment in url
    ]


@pytest.fixture
def refresh_expires_once(connected, monkeypatch):
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: None)
    refresh_calls = []

    def flaky_refresh(store, client, day):
        refresh_calls.append(day)
        if len(refresh_calls) == 1:
            raise MyfitnesspalLoginError("session expired")

    monkeypatch.setattr(server.sync, "refresh_day", flaky_refresh)
    return refresh_calls


def test_log_food_retry_after_lapsed_refresh_logs_once(connected, refresh_expires_once):
    result = asyncio.run(
        server.fitness_log_food(
            query="Banana",
            food_id="111",
            weight_id="20",
            date="2026-07-08",
            ctx=FakeContext(),
        )
    )
    assert result["ok"] is True
    assert "refresh_warning" not in result
    assert len(posts_to(connected, "food/add")) == 1
    assert len(refresh_expires_once) == 2


def test_delete_food_retry_after_lapsed_refresh_removes_once(
    connected, refresh_expires_once
):
    asyncio.run(
        server.fitness_delete_food(query="coffee", date="2026-07-08", ctx=FakeContext())
    )
    assert len(posts_to(connected, "food/remove")) == 1


def test_modify_food_retry_after_lapsed_refresh_writes_once(
    connected, refresh_expires_once
):
    asyncio.run(
        server.fitness_modify_food(
            query="coffee", new_query="banana", date="2026-07-08", ctx=FakeContext()
        )
    )
    assert len(posts_to(connected, "food/remove")) == 1
    assert len(posts_to(connected, "food/add")) == 1


def test_failed_refresh_after_write_warns_instead_of_raising(connected, monkeypatch):
    monkeypatch.setattr(server.refresh, "refresh_session", lambda: None)

    def always_expired(store, client, day):
        raise MyfitnesspalLoginError("session expired")

    monkeypatch.setattr(server.sync, "refresh_day", always_expired)
    result = asyncio.run(
        server.fitness_delete_food(query="coffee", date="2026-07-08", ctx=FakeContext())
    )
    assert result["ok"] is True
    assert "don't repeat" in result["refresh_warning"]
    assert len(posts_to(connected, "food/remove")) == 1


def test_pin_tools(local_store):
    local_store.set_pin("banana", "111", "10", "Banana", "1 medium")
    local_store.set_pin("oats", "333", "40", "Oats", None)
    assert [p["query"] for p in server.fitness_list_food_pins()["pins"]] == [
        "banana",
        "oats",
    ]
    assert server.fitness_clear_food_pin(query="Banana") == {"cleared": 1}
    assert server.fitness_clear_food_pin(clear_all=True) == {"cleared": 1}
    with pytest.raises(ValueError, match="pass query"):
        server.fitness_clear_food_pin()


def test_bulk_export_reads_cache_without_client(local_store):
    local_store.upsert_nutrition("2026-07-05", calories=1500.0)
    result = asyncio.run(
        server.fitness_bulk_export(start="2026-07-01", end="2026-07-08")
    )
    assert result["count"] == 1
    assert result["days"][0]["nutrition"]["calories"] == 1500.0


def test_log_water_tool_updates_remote_and_local_total(
    local_store, client, make_response, monkeypatch
):
    client.session.route(
        "GET", "food/water", make_response(json_data={"item": {"milliliters": 480}})
    )
    client.session.route("POST", "food/water", make_response(status_code=200))
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)

    result = asyncio.run(
        server.fitness_log_water(amount=3, unit="cup", date="2026-07-08")
    )

    assert result["ok"] is True
    assert result["water_ml"] == 1200.0
    assert local_store.day_record("2026-07-08")["nutrition"]["water_ml"] == 1200.0
