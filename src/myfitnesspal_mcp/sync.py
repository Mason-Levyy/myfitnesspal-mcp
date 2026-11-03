import logging
import threading
from contextlib import contextmanager
from datetime import date, timedelta

from . import config, diary, measurements, notes, water
from .mfp_client import is_auth_error
from .store import Store

logger = logging.getLogger(__name__)

_poll_lock = threading.Lock()


@contextmanager
def tolerating_failures(description: str):
    """Auth failures propagate so the caller can refresh the session and retry;
    everything else is logged and skipped."""
    try:
        yield
    except Exception as exc:
        if is_auth_error(exc):
            raise
        logger.warning("%s failed: %s", description, exc)


def as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_number(values: dict, *keys) -> float | None:
    for key in keys:
        number = as_float(values.get(key))
        if number is not None:
            return number
    return None


def days_to_fetch(cached: set[str], lookback: int, today: date) -> list[date]:
    """Today is always refetched because the diary is live; past days only when
    the cache has not synced their diary yet."""
    past = (today - timedelta(days=offset) for offset in range(1, lookback))
    return [today] + [day for day in past if day.isoformat() not in cached]


def macros(totals: dict) -> dict:
    return {
        "calories": first_number(totals, "calories"),
        "protein": first_number(totals, "protein"),
        "carbs": first_number(totals, "carbohydrates", "carbs"),
        "fat": first_number(totals, "fat"),
    }


def nutrition_totals(foods: list[dict]) -> dict:
    totals: dict = {}
    for food in foods:
        for name, value in food["nutrition"].items():
            totals[name] = totals.get(name, 0) + value
    return totals


def refresh_day(store: Store, client, day: date) -> None:
    logged = diary.read_day(client, day)
    key = day.isoformat()

    store.upsert_nutrition(
        key,
        **macros(nutrition_totals(logged["foods"])),
        water_ml=water.get_water_ml(client, day),
        goal_calories=first_number(logged["goals"], "calories"),
    )

    store.replace_diary(
        key,
        [
            {
                "meal": food["meal"].title(),
                "name": food["name"],
                **macros(food["nutrition"]),
            }
            for food in logged["foods"]
        ],
    )

    with tolerating_failures(f"note fetch for {day}"):
        store.set_note(key, notes.get_note(client, day))

    store.mark_diary_synced(key)


def poll(
    store: Store,
    client,
    days: int | None = None,
    force: bool = False,
    today: date | None = None,
) -> None:
    today = today or date.today()
    if not force and store.last_synced_on() == today.isoformat():
        return
    with _poll_lock:
        if not force and store.last_synced_on() == today.isoformat():
            return
        _poll_window(store, client, days, today)


def _poll_window(store: Store, client, days: int | None, today: date) -> None:
    lookback = days or config.sync_days()
    window_start = today - timedelta(days=lookback - 1)
    cached = store.days_with_synced_diary(
        window_start.isoformat(), (today - timedelta(days=1)).isoformat()
    )

    for day in days_to_fetch(cached, lookback, today):
        with tolerating_failures(f"sync for {day}"):
            refresh_day(store, client, day)

    with tolerating_failures("weight measurements"):
        weights = measurements.weight_history(client, window_start, today)
        for day, value in weights.items():
            store.upsert_nutrition(day.isoformat(), weight=float(value))

    store.mark_synced(today)
