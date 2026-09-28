import sqlite3

import pytest


def test_upsert_nutrition_partial_updates(store):
    store.upsert_nutrition("2026-07-01", calories=1800.0, protein=120.0)
    store.upsert_nutrition("2026-07-01", weight=80.5)
    row = store.nutrition("2026-07-01")
    assert row["calories"] == 1800.0
    assert row["protein"] == 120.0
    assert row["weight"] == 80.5


def test_upsert_nutrition_rejects_unknown_fields(store):
    with pytest.raises(ValueError, match="unknown nutrition fields"):
        store.upsert_nutrition("2026-07-01", steps=10000)


def test_replace_diary(store):
    store.replace_diary(
        "2026-07-01", [{"meal": "Breakfast", "name": "Egg", "calories": 70}]
    )
    store.replace_diary(
        "2026-07-01",
        [
            {"meal": "Breakfast", "name": "Oats", "calories": 300},
            {"meal": "Lunch", "name": "Salad", "calories": 250},
        ],
    )
    entries = store.diary("2026-07-01")
    assert [e["name"] for e in entries] == ["Oats", "Salad"]


def test_feel_upsert(store):
    store.set_feel("2026-07-01", "tired", 2)
    store.set_feel("2026-07-01", "better after coffee", 4)
    assert store.feel("2026-07-01") == {
        "day": "2026-07-01",
        "note": "better after coffee",
        "rating": 4,
    }
    assert store.feel("2026-07-02") is None


def test_note_upsert(store):
    store.set_note("2026-07-01", "first draft")
    store.set_note("2026-07-01", "final note")
    assert store.note("2026-07-01") == "final note"
    assert store.note("2026-07-02") is None


def test_trend_filters_nulls_and_orders(store):
    store.upsert_nutrition("2026-07-02", weight=81.0)
    store.upsert_nutrition("2026-07-01", weight=80.0)
    store.upsert_nutrition("2026-07-03", calories=2000.0)
    points = store.trend("weight", "2026-07-01", "2026-07-31")
    assert points == [
        {"day": "2026-07-01", "value": 80.0},
        {"day": "2026-07-02", "value": 81.0},
    ]


def test_trend_unknown_metric(store):
    with pytest.raises(ValueError, match="unknown metric"):
        store.trend("steps", "2026-07-01", "2026-07-31")


def test_days_with_synced_diary(store):
    store.mark_diary_synced("2026-07-01")
    store.mark_diary_synced("2026-07-05")
    assert store.days_with_synced_diary("2026-07-01", "2026-07-04") == {"2026-07-01"}


def test_partial_row_is_not_a_synced_diary(store):
    """A weigh-in creates a row before the diary is ever fetched; that row must
    not hide the day from gap-fill."""
    store.upsert_nutrition("2026-07-01", weight=80.0)
    assert store.days_with_synced_diary("2026-07-01", "2026-07-01") == set()

    store.mark_diary_synced("2026-07-01")
    store.upsert_nutrition("2026-07-01", weight=79.5)
    assert store.days_with_synced_diary("2026-07-01", "2026-07-01") == {"2026-07-01"}


def test_nutrition_hides_sync_bookkeeping(store):
    store.upsert_nutrition("2026-07-01", calories=1800.0)
    store.mark_diary_synced("2026-07-01")
    assert "diary_synced" not in store.nutrition("2026-07-01")


def test_migrates_pre_diary_synced_database(tmp_path):
    from myfitnesspal_mcp.store import Store

    path = tmp_path / "old.db"
    legacy = sqlite3.connect(str(path))
    legacy.executescript(
        """
        CREATE TABLE day_nutrition (
            day TEXT PRIMARY KEY, calories REAL, protein REAL, carbs REAL,
            fat REAL, water_ml REAL, weight REAL, goal_calories REAL
        );
        INSERT INTO day_nutrition (day, calories) VALUES ('2026-07-01', 2000.0);
        INSERT INTO day_nutrition (day, water_ml) VALUES ('2026-07-02', 500.0);
        INSERT INTO day_nutrition (day, weight) VALUES ('2026-07-03', 80.0);
        """
    )
    legacy.commit()
    legacy.close()

    store = Store(path)
    assert store.days_with_synced_diary("2026-07-01", "2026-07-03") == {
        "2026-07-01",
        "2026-07-02",
    }
    assert store.nutrition("2026-07-03")["weight"] == 80.0


def test_export_range_unions_sources(store):
    store.upsert_nutrition("2026-07-01", calories=2000.0)
    store.replace_diary("2026-07-02", [{"meal": "Lunch", "name": "Soup"}])
    store.set_feel("2026-07-03", "good", 5)
    store.set_note("2026-07-04", "walked 10k steps")
    days = store.export_range("2026-07-01", "2026-07-31")
    assert [d["day"] for d in days] == [
        "2026-07-01",
        "2026-07-02",
        "2026-07-03",
        "2026-07-04",
    ]
    assert days[1]["diary"][0]["name"] == "Soup"
    assert days[2]["feel"]["rating"] == 5
    assert days[3]["note"] == "walked 10k steps"


def test_export_range_ignores_empty_notes(store):
    store.set_note("2026-07-05", None)
    store.set_note("2026-07-06", "")
    assert store.export_range("2026-07-01", "2026-07-31") == []


def test_mark_synced_roundtrip(store):
    assert store.last_synced_on() is None
    store.mark_synced()
    assert store.last_synced_on() is not None
