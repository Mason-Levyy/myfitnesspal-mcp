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


class FakeSyncClient:
    def __init__(self):
        self.fetched = []
        self.weights = {}

    def get_date(self, day):
        self.fetched.append(day)
        return FakeDay()

    def get_measurements(self, kind, earliest):
        return self.weights


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


def test_poll_skips_when_synced_today(store, monkeypatch):
    client = FakeSyncClient()
    store.mark_synced()
    sync.poll(store, client)
    assert client.fetched == []
    sync.poll(store, client, force=True, days=1)
    assert client.fetched != []


def test_poll_records_weights(store):
    client = FakeSyncClient()
    client.weights = {TODAY: 80.0, TODAY - datetime.timedelta(days=400): 90.0}
    sync.poll(store, client, days=3, force=True)
    assert store.nutrition(TODAY.isoformat())["weight"] == 80.0


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
