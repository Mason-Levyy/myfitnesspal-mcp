import datetime
import threading

import pytest
from myfitnesspal.exceptions import MyfitnesspalLoginError

from myfitnesspal_mcp import sync

TODAY = datetime.date(2026, 7, 8)


def test_days_to_fetch_gaps_only():
    existing = {"2026-07-07", "2026-07-05"}
    days = sync.days_to_fetch(existing, lookback=4, today=TODAY)
    assert days == [TODAY, datetime.date(2026, 7, 6)]


def test_days_to_fetch_always_includes_today():
    days = sync.days_to_fetch({TODAY.isoformat()}, lookback=1, today=TODAY)
    assert days == [TODAY]


def test_first_number_prefers_first_present_key():
    assert sync.first_number({"carbohydrates": 10}, "carbohydrates", "carbs") == 10.0
    assert sync.first_number({"carbs": 5}, "carbohydrates", "carbs") == 5.0
    assert sync.first_number({}, "carbohydrates", "carbs") is None


class FakeEntry:
    def __init__(self, name, totals):
        self.name = name
        self.totals = totals


class FakeMeal:
    def __init__(self, name, entries):
        self.name = name
        self.entries = entries


class FakeDay:
    def __init__(self):
        self.totals = {
            "calories": 2100,
            "protein": 150,
            "carbohydrates": 200,
            "fat": 70,
        }
        self.goals = {"calories": 2200}
        self.water = 750
        self.complete = False
        self.meals = [
            FakeMeal(
                "breakfast", [FakeEntry("Oats", {"calories": 300, "protein": 10})]
            ),
            FakeMeal("lunch", []),
        ]


class FakeNoteResponse:
    def __init__(self, body):
        self._body = body

    def json(self):
        return {"item": {"body": self._body}}

    def raise_for_status(self):
        pass


class FakeNoteSession:
    def __init__(self, body):
        self.body = body

    def get(self, url, **kwargs):
        return FakeNoteResponse(self.body)


class FakeSyncClient:
    BASE_URL_SECURE = "https://www.myfitnesspal.com/"

    def __init__(self, note_body="today felt great"):
        self.fetched = []
        self.weights = {}
        self.access_token = "fake-token"
        self.user_id = "user-1"
        self.effective_username = "tester"
        self.session = FakeNoteSession(note_body)

    def get_date(self, day):
        self.fetched.append(day)
        return FakeDay()

    def get_measurements(self, kind, earliest):
        self.measurements_earliest = earliest
        return {day: value for day, value in self.weights.items() if day >= earliest}


def test_refresh_day_populates_store(store):
    client = FakeSyncClient()
    sync.refresh_day(store, client, TODAY)
    nutrition = store.nutrition(TODAY.isoformat())
    assert nutrition["calories"] == 2100.0
    assert nutrition["carbs"] == 200.0
    assert nutrition["water_ml"] == 750.0
    assert nutrition["goal_calories"] == 2200.0
    entries = store.diary(TODAY.isoformat())
    assert entries == [
        {
            "meal": "Breakfast",
            "name": "Oats",
            "calories": 300.0,
            "protein": 10.0,
            "carbs": None,
            "fat": None,
        }
    ]
    assert store.note(TODAY.isoformat()) == "today felt great"


def test_refresh_day_stores_the_macro_goals(store):
    client = FakeSyncClient()
    goal_day = FakeDay()
    goal_day.goals = {"calories": 2200, "protein": 160, "carbohydrates": 220, "fat": 70}
    client.get_date = lambda day: goal_day
    sync.refresh_day(store, client, TODAY)
    assert store.goals(TODAY.isoformat()) == {
        "calories": 2200.0,
        "protein": 160.0,
        "carbs": 220.0,
        "fat": 70.0,
    }


def test_poll_skips_when_synced_today(store, monkeypatch):
    client = FakeSyncClient()
    store.mark_synced(TODAY)
    sync.poll(store, client, today=TODAY)
    assert client.fetched == []
    sync.poll(store, client, force=True, days=1, today=TODAY)
    assert client.fetched != []


def test_concurrent_polls_fetch_once(store):
    today_fetch_started = threading.Event()
    let_fetch_finish = threading.Event()

    class SlowClient(FakeSyncClient):
        def get_date(self, day):
            if day == TODAY:
                today_fetch_started.set()
                let_fetch_finish.wait(5)
            return super().get_date(day)

    client = SlowClient()
    first = threading.Thread(target=sync.poll, args=(store, client, 2, False, TODAY))
    first.start()
    assert today_fetch_started.wait(5)
    second = threading.Thread(target=sync.poll, args=(store, client, 2, False, TODAY))
    second.start()
    let_fetch_finish.set()
    first.join(5)
    second.join(5)

    assert client.fetched.count(TODAY) == 1


def test_poll_records_weights(store):
    client = FakeSyncClient()
    client.weights = {TODAY: 80.0, TODAY - datetime.timedelta(days=400): 90.0}
    sync.poll(store, client, days=3, force=True, today=TODAY)
    assert store.nutrition(TODAY.isoformat())["weight"] == 80.0


def test_poll_backfills_weight_for_already_cached_day(store):
    """A weigh-in lands on a day whose nutrition is already cached.

    Such a day is absent from `days_to_fetch`, so keying the measurement query
    off the fetched days would strand the weight permanently.
    """
    window_start = TODAY - datetime.timedelta(days=2)
    yesterday = TODAY - datetime.timedelta(days=1)
    for day in (window_start, yesterday):
        store.upsert_nutrition(day.isoformat(), calories=2000.0, diary_complete=1)
        store.mark_diary_synced(day.isoformat())

    client = FakeSyncClient()
    client.weights = {yesterday: 79.5}
    sync.poll(store, client, days=3, force=True, today=TODAY)

    assert client.fetched == [TODAY]
    assert client.measurements_earliest == window_start
    assert store.nutrition(yesterday.isoformat())["weight"] == 79.5


def test_poll_fetches_past_day_that_only_has_a_weigh_in(store):
    """`fitness_log_weight` on a past date writes a weight-only row. Gap-fill
    must still fetch that day's diary rather than treat the row as cached."""
    yesterday = TODAY - datetime.timedelta(days=1)
    store.upsert_nutrition(yesterday.isoformat(), weight=80.0)

    client = FakeSyncClient()
    sync.poll(store, client, days=2, force=True, today=TODAY)

    assert client.fetched == [TODAY, yesterday]
    nutrition = store.nutrition(yesterday.isoformat())
    assert nutrition["calories"] == 2100.0
    assert nutrition["weight"] == 80.0


def test_poll_retries_day_whose_fetch_failed(store):
    """A transient failure in refresh_day followed by the weight backfill used
    to leave a weight-only row that blocked every later gap-fill."""
    yesterday = TODAY - datetime.timedelta(days=1)

    class FlakyClient(FakeSyncClient):
        def __init__(self):
            super().__init__()
            self.failing = {yesterday}

        def get_date(self, day):
            if day in self.failing:
                raise RuntimeError("MFP returned 502")
            return super().get_date(day)

    client = FlakyClient()
    client.weights = {yesterday: 80.0}
    sync.poll(store, client, days=2, force=True, today=TODAY)
    assert store.nutrition(yesterday.isoformat())["calories"] is None

    client.failing.clear()
    client.fetched.clear()
    sync.poll(store, client, days=2, force=True, today=TODAY)
    assert yesterday in client.fetched
    assert store.nutrition(yesterday.isoformat())["calories"] == 2100.0


def test_poll_propagates_auth_errors(store):
    class ExpiredClient(FakeSyncClient):
        def get_date(self, day):
            raise MyfitnesspalLoginError("session expired")

    with pytest.raises(MyfitnesspalLoginError):
        sync.poll(store, ExpiredClient(), days=2, force=True)


def test_poll_skips_bad_days_without_auth_errors(store):
    class FlakyClient(FakeSyncClient):
        def get_date(self, day):
            self.fetched.append(day)
            if len(self.fetched) == 1:
                raise ValueError("parse error")
            return FakeDay()

    client = FlakyClient()
    sync.poll(store, client, days=2, force=True)
    assert len(client.fetched) == 2


def cache_synced_days(store, days, complete):
    for day in days:
        store.upsert_nutrition(
            day.isoformat(), calories=2000.0, diary_complete=complete
        )
        store.mark_diary_synced(day.isoformat())


def test_refresh_day_stores_completion(store):
    client = FakeSyncClient()
    complete_day = FakeDay()
    complete_day.complete = True
    client.get_date = lambda day: complete_day
    sync.refresh_day(store, client, TODAY)
    assert store.day_record(TODAY.isoformat())["complete"] is True


def test_poll_fetches_days_cached_before_completion_was_tracked_once(store):
    old_days = [TODAY - datetime.timedelta(days=offset) for offset in (10, 11)]
    cache_synced_days(store, old_days, complete=None)

    client = FakeSyncClient()
    sync.poll(store, client, days=14, force=True, today=TODAY)
    assert set(old_days) <= set(client.fetched)

    client.fetched.clear()
    sync.poll(store, client, days=14, force=True, today=TODAY)
    assert not set(old_days) & set(client.fetched)


def test_poll_rechecks_recent_days_not_yet_complete(store):
    incomplete_recent = TODAY - datetime.timedelta(days=2)
    complete_recent = TODAY - datetime.timedelta(days=3)
    incomplete_old = TODAY - datetime.timedelta(days=sync.RECHECK_INCOMPLETE_DAYS + 2)
    cache_synced_days(store, [incomplete_recent, incomplete_old], complete=0)
    cache_synced_days(store, [complete_recent], complete=1)

    client = FakeSyncClient()
    sync.poll(store, client, days=14, force=True, today=TODAY)
    assert incomplete_recent in client.fetched
    assert complete_recent not in client.fetched
    assert incomplete_old not in client.fetched
