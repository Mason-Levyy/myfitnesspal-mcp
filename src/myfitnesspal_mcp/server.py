import asyncio
import datetime
from typing import Any, Callable

from mcp.server.fastmcp import Context, FastMCP

from . import diary, mfp_client, refresh, sync
from .store import Store, TREND_COLUMNS

mcp = FastMCP("myfitnesspal")

_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def parse_day(value: str | None) -> datetime.date:
    if value is None:
        return datetime.date.today()
    return datetime.date.fromisoformat(value)


async def run_with_refresh(ctx: Context, op: Callable[[], Any]) -> Any:
    """Runs a blocking MFP operation; on an auth-shaped failure, notifies the
    client, refreshes the session (headless browser profile when available,
    otherwise re-reads MFP_COOKIE / cookies.json), and retries once."""
    try:
        return await asyncio.to_thread(op)
    except Exception as exc:
        if not mfp_client.is_auth_error(exc):
            raise
        await ctx.info(
            "MyFitnessPal rejected the session — refreshing credentials and retrying."
        )
        try:
            await asyncio.to_thread(refresh.refresh_session)
            result = await asyncio.to_thread(op)
        except Exception as retry_exc:
            await ctx.info("Session refresh failed.")
            raise RuntimeError(
                f"{mfp_client.RECONNECT_HINT} (retry after refresh failed: {retry_exc})"
            ) from retry_exc
        await ctx.info("Session refreshed; the retried call succeeded.")
        return result


def day_summary(store: Store, day: datetime.date) -> dict:
    key = day.isoformat()
    return {
        "day": key,
        "nutrition": store.nutrition(key),
        "diary": store.diary(key),
        "note": store.note(key),
        "feel": store.feel(key),
    }


@mcp.tool()
async def fitness_get_day(date: str | None = None, ctx: Context = None) -> dict:
    """Nutrition summary, diary entries, the MyFitnessPal daily note, and the
    local feel note for a day.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        sync.poll(store, client)
        sync.refresh_day(store, client, day)
        return day_summary(store, day)

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_search_food(
    query: str, limit: int = 5, with_macros: bool = True, ctx: Context = None
) -> dict:
    """Search MyFitnessPal's food database and return candidate matches.

    Each candidate has name, brand, calories, macros, serving, and the
    food_id + weight_id to pass to fitness_log_food to log exactly that item.
    """

    def op():
        client = mfp_client.get_client()
        return {"query": query, "results": diary.search_food(client, query, limit, with_macros)}

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_log_food(
    query: str,
    meal: str = "breakfast",
    quantity: float = 1.0,
    date: str | None = None,
    food_id: str | None = None,
    weight_id: str | None = None,
    ctx: Context = None,
) -> dict:
    """Log a food to the real MyFitnessPal diary.

    Searches for `query` and logs the top match. To log an exact item, pass
    the food_id + weight_id of a fitness_search_food candidate (query is then
    used as the display name). meal: breakfast|lunch|dinner|snacks.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        result = diary.push_food(
            client, day, meal, query, quantity, food_id=food_id, weight_id=weight_id
        )
        sync.refresh_day(store, client, day)
        return {"ok": True, **result, "day": day_summary(store, day)}

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_delete_food(
    query: str, meal: str | None = None, date: str | None = None, ctx: Context = None
) -> dict:
    """Remove a food from the MyFitnessPal diary by name match.

    query: text matched against logged entry names (e.g. "banana").
    meal: optional breakfast|lunch|dinner|snacks to disambiguate duplicates.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        result = diary.delete_food(client, day, query, meal)
        sync.refresh_day(store, client, day)
        return {"ok": True, **result}

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_modify_food(
    query: str,
    new_query: str | None = None,
    meal: str = "breakfast",
    quantity: float = 1.0,
    date: str | None = None,
    ctx: Context = None,
) -> dict:
    """Replace a MyFitnessPal diary entry: deletes the match, then adds a food.

    query: the existing entry to replace (name match) within `meal`.
    new_query: the food to add instead; omit to re-add `query` (e.g. to change
    quantity). meal: breakfast|lunch|dinner|snacks. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        result = diary.modify_food(client, day, meal, query, new_query, quantity)
        sync.refresh_day(store, client, day)
        return {"ok": True, **result}

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_log_weight(
    weight: float, date: str | None = None, ctx: Context = None
) -> dict:
    """Log a weight measurement to MyFitnessPal.

    weight: in your MyFitnessPal account's display unit (kg or lbs).
    Logging twice for the same date updates that day's measurement.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        result = diary.set_weight(client, day, weight)
        store.upsert_nutrition(day.isoformat(), weight=result["weight"])
        return {"ok": True, **result}

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_get_exercise(date: str | None = None, ctx: Context = None) -> dict:
    """Read the MyFitnessPal exercise diary (cardio + strength) for a day.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        client = mfp_client.get_client()
        return diary.get_exercise(client, day)

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_get_note(date: str | None = None, ctx: Context = None) -> dict:
    """Read the MyFitnessPal daily diary note (the free-text 'Notes' box at the
    bottom of the day) straight from your account.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        body = diary.get_note(client, day)
        store.set_note(day.isoformat(), body)
        return {"day": day.isoformat(), "note": body}

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_log_note(
    text: str, date: str | None = None, append: bool = False, ctx: Context = None
) -> dict:
    """Write the MyFitnessPal daily diary note (the free-text 'Notes' box at the
    bottom of the day). This is your real MFP note, synced to your account —
    distinct from the local-only fitness_log_feel.

    text: the note body. append: add to the existing note on a new line instead
    of replacing it. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        result = diary.push_note(client, day, text, append=append)
        store.set_note(day.isoformat(), result["note"])
        return {"ok": True, **result}

    return await run_with_refresh(ctx, op)


@mcp.tool()
def fitness_log_feel(
    note: str | None = None, rating: int | None = None, date: str | None = None
) -> dict:
    """Save a 'how I feel today' note. Stored locally only — never sent to
    MyFitnessPal.

    rating: optional 1-5. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    return get_store().set_feel(day.isoformat(), note, rating)


@mcp.tool()
async def fitness_get_trends(
    metric: str, start: str | None = None, end: str | None = None, ctx: Context = None
) -> dict:
    """A single metric over a date range, for charts/analysis.

    metric: weight | calories_in | protein | carbs | fat.
    start/end: YYYY-MM-DD (default: last 30 days).
    Returns {metric, points: [{day, value}, ...]} with nulls omitted.
    """
    if metric not in TREND_COLUMNS:
        raise ValueError(f"unknown metric (use {' | '.join(TREND_COLUMNS)})")
    end_day = parse_day(end)
    if start is None:
        start_day = end_day - datetime.timedelta(days=30)
    else:
        start_day = parse_day(start)

    def op():
        store = get_store()
        client = mfp_client.get_client()
        sync.poll(store, client)
        return {
            "metric": metric,
            "points": store.trend(metric, start_day.isoformat(), end_day.isoformat()),
        }

    return await run_with_refresh(ctx, op)


@mcp.tool()
async def fitness_bulk_export(
    start: str | None = None, end: str | None = None, sync_first: bool = False,
    ctx: Context = None,
) -> dict:
    """Export a whole date range at once for analysis: per-day nutrition
    totals, food entries with macros, the MyFitnessPal daily note, and local
    feel notes. Read-only.

    start/end: YYYY-MM-DD (default: last 30 days ending today).
    sync_first: gap-fill from MyFitnessPal before exporting. Off by default so
    large historical exports stay fast on cached data.
    """
    end_day = parse_day(end)
    if start is None:
        start_day = end_day - datetime.timedelta(days=30)
    else:
        start_day = parse_day(start)
    if start_day > end_day:
        raise ValueError("start must be on or before end")

    def op():
        store = get_store()
        if sync_first:
            client = mfp_client.get_client()
            span = (end_day - start_day).days + 1
            sync.poll(store, client, days=span, force=True)
        days = store.export_range(start_day.isoformat(), end_day.isoformat())
        return {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "count": len(days),
            "days": days,
        }

    return await run_with_refresh(ctx, op)
