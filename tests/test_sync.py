import datetime

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
        self.totals = {"calories": 2100, "protein": 150, "carbohydrates": 200, "fat": 70}
        self.goals = {"calories": 2200}
        self.water = 750
        self.meals = [
            FakeMeal("breakfast", [FakeEntry("Oats", {"calories": 300, "protein": 10})]),
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


def test_poll_skips_when_synced_today(store, monkeypatch):
    client = FakeSyncClient()
    store.mark_synced(TODAY)
    sync.poll(store, client, today=TODAY)
    assert client.fetched == []
    sync.poll(store, client, force=True, days=1, today=TODAY)
    assert client.fetched != []


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
        store.upsert_nutrition(day.isoformat(), calories=2000.0)

    client = FakeSyncClient()
    client.weights = {yesterday: 79.5}
    sync.poll(store, client, days=3, force=True, today=TODAY)

    assert client.fetched == [TODAY]
    assert client.measurements_earliest == window_start
    assert store.nutrition(yesterday.isoformat())["weight"] == 79.5


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
