import logging
from contextlib import contextmanager
from datetime import date, timedelta

from . import config, diary
from .mfp_client import is_auth_error
from .store import Store

logger = logging.getLogger(__name__)


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
    the cache has no row for them."""
    past = (today - timedelta(days=offset) for offset in range(1, lookback))
    return [today] + [day for day in past if day.isoformat() not in cached]


def macros(totals: dict) -> dict:
    return {
        "calories": first_number(totals, "calories"),
        "protein": first_number(totals, "protein"),
        "carbs": first_number(totals, "carbohydrates", "carbs"),
        "fat": first_number(totals, "fat"),
    }


def refresh_day(store: Store, client, day: date) -> None:
    mfp_day = client.get_date(day)
    key = day.isoformat()

    store.upsert_nutrition(
        key,
        **macros(mfp_day.totals),
        water_ml=as_float(mfp_day.water),
        goal_calories=first_number(mfp_day.goals or {}, "calories"),
    )

    store.replace_diary(
        key,
        [
            {"meal": str(meal.name).title(), "name": entry.name, **macros(entry.totals)}
            for meal in mfp_day.meals
            for entry in meal.entries
        ],
    )

    with tolerating_failures(f"note fetch for {day}"):
        store.set_note(key, diary.get_note(client, day))


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

    lookback = days or config.sync_days()
    window_start = today - timedelta(days=lookback - 1)
    cached = store.days_with_nutrition(
        window_start.isoformat(), (today - timedelta(days=1)).isoformat()
    )

    for day in days_to_fetch(cached, lookback, today):
        with tolerating_failures(f"sync for {day}"):
            refresh_day(store, client, day)

    with tolerating_failures("weight measurements"):
        weights = client.get_measurements("Weight", window_start)
        for day, value in weights.items():
            if day >= window_start:
                store.upsert_nutrition(day.isoformat(), weight=float(value))

    store.mark_synced(today)
