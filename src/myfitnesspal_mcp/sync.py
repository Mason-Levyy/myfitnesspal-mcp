import logging
from datetime import date, timedelta

from . import config
from .mfp_client import is_auth_error
from .store import Store

logger = logging.getLogger(__name__)


def first_number(values: dict, *keys) -> float | None:
    for key in keys:
        value = values.get(key)
        if value is not None:
            return float(value)
    return None


def days_to_fetch(existing: set[str], lookback: int, today: date) -> list[date]:
    """Today is always refetched (the diary is live); past days only when the
    cache has no row for them."""
    days = []
    for offset in range(lookback):
        day = today - timedelta(days=offset)
        if day == today or day.isoformat() not in existing:
            days.append(day)
    return days


def refresh_day(store: Store, client, day: date) -> None:
    mfp_day = client.get_date(day)
    totals = mfp_day.totals
    goals = mfp_day.goals or {}

    water_ml = None
    try:
        if mfp_day.water is not None:
            water_ml = float(mfp_day.water)
    except Exception:
        water_ml = None

    store.upsert_nutrition(
        day.isoformat(),
        calories=first_number(totals, "calories"),
        protein=first_number(totals, "protein"),
        carbs=first_number(totals, "carbohydrates", "carbs"),
        fat=first_number(totals, "fat"),
        water_ml=water_ml,
        goal_calories=first_number(goals, "calories"),
    )

    entries = []
    for meal in mfp_day.meals:
        for entry in meal.entries:
            entry_totals = entry.totals
            entries.append(
                {
                    "meal": str(meal.name).title(),
                    "name": entry.name,
                    "calories": first_number(entry_totals, "calories"),
                    "protein": first_number(entry_totals, "protein"),
                    "carbs": first_number(entry_totals, "carbohydrates", "carbs"),
                    "fat": first_number(entry_totals, "fat"),
                }
            )
    store.replace_diary(day.isoformat(), entries)


def poll(store: Store, client, days: int | None = None, force: bool = False) -> None:
    if not force and store.last_synced_on() == date.today().isoformat():
        return

    lookback = days or config.sync_days()
    today = date.today()
    window_start = today - timedelta(days=lookback - 1)
    existing = store.days_with_nutrition(
        window_start.isoformat(), (today - timedelta(days=1)).isoformat()
    )

    fetched = days_to_fetch(existing, lookback, today)
    for day in fetched:
        try:
            refresh_day(store, client, day)
        except Exception as exc:
            if is_auth_error(exc):
                raise
            logger.warning("skipping %s: %s", day, exc)

    if fetched:
        try:
            weights = client.get_measurements("Weight", min(fetched))
            for day, value in weights.items():
                if day >= window_start:
                    store.upsert_nutrition(day.isoformat(), weight=float(value))
        except Exception as exc:
            if is_auth_error(exc):
                raise
            logger.warning("weight measurements failed: %s", exc)

    store.mark_synced()
