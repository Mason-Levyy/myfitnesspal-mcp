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
    store.replace_diary("2026-07-01", [{"meal": "Breakfast", "name": "Egg", "calories": 70}])
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


def test_days_with_nutrition(store):
    store.upsert_nutrition("2026-07-01", calories=1.0)
    store.upsert_nutrition("2026-07-05", calories=1.0)
    assert store.days_with_nutrition("2026-07-01", "2026-07-04") == {"2026-07-01"}


def test_export_range_unions_sources(store):
    store.upsert_nutrition("2026-07-01", calories=2000.0)
    store.replace_diary("2026-07-02", [{"meal": "Lunch", "name": "Soup"}])
    store.set_feel("2026-07-03", "good", 5)
    days = store.export_range("2026-07-01", "2026-07-31")
    assert [d["day"] for d in days] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert days[1]["diary"][0]["name"] == "Soup"
    assert days[2]["feel"]["rating"] == 5


def test_mark_synced_roundtrip(store):
    assert store.last_synced_on() is None
    store.mark_synced()
    assert store.last_synced_on() is not None
