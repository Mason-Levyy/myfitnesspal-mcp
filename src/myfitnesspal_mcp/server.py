import asyncio
import datetime
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import (
    custom_items,
    custom_writes,
    day_completion,
    diary,
    food_logging,
    mfp_client,
    refresh,
    sync,
)
from .food_ranking import MacroTargets
from .store import Store, trend_column

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


def parse_range(
    start: str | None, end: str | None, span_days: int = 30
) -> tuple[datetime.date, datetime.date]:
    end_day = parse_day(end)
    if start is None:
        start_day = end_day - datetime.timedelta(days=span_days)
    else:
        start_day = parse_day(start)
    if start_day > end_day:
        raise ValueError("start must be on or before end")
    return start_day, end_day


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


async def with_session(ctx: Context, op: Callable[[Store, Any], Any]) -> Any:
    """Runs `op` against the store and a live MFP client, re-resolving both on
    the retry so a refreshed session is picked up."""
    return await run_with_refresh(ctx, lambda: op(get_store(), mfp_client.get_client()))


async def refresh_after_write(ctx: Context, day: datetime.date) -> dict:
    def refresh_op(store, client):
        sync.refresh_day(store, client, day)

    try:
        await with_session(ctx, refresh_op)
    except Exception as exc:
        return {
            "refresh_warning": (
                f"the diary change was saved, but refreshing the local copy of "
                f"{day.isoformat()} failed ({exc}); don't repeat the change"
            )
        }
    return {}


@mcp.tool()
async def fitness_get_day(date: str | None = None, ctx: Context = None) -> dict:
    """Nutrition summary, diary entries, the MyFitnessPal daily note, and the
    local feel note for a day.

    `goals` are the day's MyFitnessPal targets (calories, protein, carbs, fat)
    and `remaining` is goal minus what is logged (negative: over the goal);
    null where MyFitnessPal has no goal. `complete` says whether the day is
    marked complete in MyFitnessPal (null: not known yet).

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        sync.poll(store, client)
        sync.refresh_day(store, client, day)
        return store.day_record(day.isoformat())

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_search_food(
    query: str, limit: int = 5, with_macros: bool = True, ctx: Context = None
) -> dict:
    """Search MyFitnessPal's food database and return candidate matches.

    Each candidate has name, brand, calories, macros, serving, and the
    food_id + weight_id to pass to fitness_log_food to log exactly that item.
    external_id is the id fitness_create_recipe takes for an ingredient.
    """

    def op(store, client):
        return {
            "query": query,
            "results": diary.search_food(client, query, limit, with_macros),
        }

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_draft_food(
    query: str,
    quantity: float = 1.0,
    meal: str = "breakfast",
    date: str | None = None,
    min_calories: float | None = None,
    max_calories: float | None = None,
    min_protein: float | None = None,
    max_protein: float | None = None,
    min_carbs: float | None = None,
    max_carbs: float | None = None,
    min_fat: float | None = None,
    max_fat: float | None = None,
    limit: int = 10,
    ctx: Context = None,
) -> dict:
    """Draft a food entry: numbered options to choose from before logging.

    Each option lists every serving size with its calories/protein/carbs/fat
    for the whole entry (serving × quantity) and a suggested_serving. The
    min_/max_ targets (grams; calories in kcal) also apply to the whole
    entry: options that fit come first, near misses follow, flagged
    fits_targets=false. A food you've confirmed before for this query is
    pinned to the top. The order is fixed once drafted.

    Log a choice with fitness_log_food(draft_id=..., option=N, serving=M);
    call this again with different targets to refine. Drafts last 24 hours.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)
    targets = MacroTargets(
        min_calories=min_calories,
        max_calories=max_calories,
        min_protein=min_protein,
        max_protein=max_protein,
        min_carbs=min_carbs,
        max_carbs=max_carbs,
        min_fat=min_fat,
        max_fat=max_fat,
    )

    def op(store, client):
        return food_logging.draft_food(
            client, store, query, day, meal, quantity, targets, limit
        )

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_log_food(
    query: str | None = None,
    meal: str | None = None,
    quantity: float | None = None,
    date: str | None = None,
    draft_id: str | None = None,
    option: int | None = None,
    serving: int | None = None,
    pin: bool = True,
    food_id: str | None = None,
    weight_id: str | None = None,
    ctx: Context = None,
) -> dict:
    """Log a food to the real MyFitnessPal diary.

    Preferred: pick from a fitness_draft_food draft with draft_id + option
    (+ serving, default the option's suggested_serving). meal, quantity, and
    date default to the draft's. pin=True remembers the choice so logging
    the same query later reuses this food and serving.

    With only `query`: logs a pinned food, or the single exact-name match;
    otherwise nothing is logged and a draft is returned (needs_choice=true)
    to pick from. With food_id + weight_id (from fitness_search_food): logs
    exactly that item, `query` being its display name.

    A logged result lists `added_entries` (meal, name): what MyFitnessPal
    actually put in the diary. MyFitnessPal accepts any food_id, so if the
    names are not the food you meant, delete them. An error saying no new
    entry appeared means nothing was logged.

    meal: breakfast|lunch|dinner|snacks (default breakfast) or any meal name
    on the account. quantity: number of servings (default 1).
    date: YYYY-MM-DD (default today).
    """
    explicit_day = parse_day(date) if date else None

    def op(store, client):
        if draft_id is not None:
            if option is None:
                raise ValueError("option is required with draft_id")
            result = food_logging.log_from_draft(
                client,
                store,
                draft_id,
                option,
                serving=serving,
                quantity=quantity,
                meal=meal,
                day=explicit_day,
                pin=pin,
            )
        elif food_id is not None and weight_id is not None:
            food = {
                "food_id": food_id,
                "weight_id": weight_id,
                "name": query or food_id,
            }
            logged = food_logging.log_exact(
                client,
                food,
                explicit_day or parse_day(None),
                meal or "breakfast",
                1.0 if quantity is None else quantity,
            )
            result = {**logged, "source": "ids"}
        elif query:
            result = food_logging.log_by_query(
                client,
                store,
                query,
                explicit_day or parse_day(None),
                meal or "breakfast",
                1.0 if quantity is None else quantity,
            )
        else:
            raise ValueError("pass draft_id + option, food_id + weight_id, or query")
        return result

    result = await with_session(ctx, op)
    if not result.get("logged"):
        return {"ok": True, **result}
    refresh_warning = await refresh_after_write(
        ctx, datetime.date.fromisoformat(result["date"])
    )
    return {
        "ok": True,
        **result,
        **refresh_warning,
        "day": get_store().day_record(result["date"]),
    }


@mcp.tool()
async def fitness_complete_day(
    complete: bool | None = True, date: str | None = None, ctx: Context = None
) -> dict:
    """Mark a MyFitnessPal diary day complete (the Food tab's "Complete This
    Entry" button), or reopen it.

    complete: true marks it complete, false reopens it ("Make Additional
    Entries"), null only reports whether it is complete. date: YYYY-MM-DD
    (default: today). Completing a day posts it to the user's MyFitnessPal
    news feed with a five-week weight projection (MFP skips both when the day
    is under its calorie minimum), so complete a day only when the user asks.
    `message` is what MFP shows. The result is read back from the diary.
    """
    day = parse_day(date)

    def op(store, client):
        result = day_completion.set_day_complete(client, day, complete)
        store.upsert_nutrition(day.isoformat(), diary_complete=int(result["complete"]))
        return result

    return {"ok": True, **await with_session(ctx, op)}


@mcp.tool()
async def fitness_list_custom(
    kind: str = "all", query: str | None = None, ctx: Context = None
) -> dict:
    """List your own MyFitnessPal items: custom foods ("My Foods"), saved meals
    and recipes.

    kind: all | foods | meals | recipes. query: optional case-insensitive
    name filter. Each item has kind, name, food_id and servings
    [{weight_id, serving}] (these ids also work with fitness_log_food), plus
    nutrition; meals also list their ingredients, recipes their recipe_id and
    per-serving nutrition. If nutrition could not be read, `warnings` says so
    and names and ids still work. To log one by name, use fitness_log_custom.
    """

    def op(store, client):
        return custom_items.list_custom(client, kind, query)

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_log_custom(
    name: str,
    meal: str = "breakfast",
    quantity: float = 1.0,
    serving: str | None = None,
    kind: str = "all",
    date: str | None = None,
    ctx: Context = None,
) -> dict:
    """Log one of your custom foods, saved meals or recipes by name.

    name: the item's name as fitness_list_custom shows it (a unique part of it
    works). A saved meal is logged as its ingredients. serving: one of the
    item's servings by name (default: its first). quantity: how many servings.
    kind: all | foods | meals | recipes, to narrow the name match.
    meal: breakfast|lunch|dinner|snacks or any meal name on the account.
    date: YYYY-MM-DD (default: today).
    `added_entries` lists the diary entries MyFitnessPal actually added.
    """
    day = parse_day(date)

    def op(store, client):
        return custom_items.log_custom(
            client, day, meal, name, kind=kind, quantity=quantity, serving=serving
        )

    result = await with_session(ctx, op)
    return {
        "ok": True,
        **result,
        **await refresh_after_write(ctx, day),
        "day": get_store().day_record(day.isoformat()),
    }


@mcp.tool()
async def fitness_create_food(
    description: str,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    brand: str | None = None,
    serving_size: float = 1,
    serving_unit: str = "serving",
    fiber: float | None = None,
    sugar: float | None = None,
    sodium: float | None = None,
    saturated_fat: float | None = None,
    ctx: Context = None,
) -> dict:
    """Create a private custom food in your MyFitnessPal "My Foods".

    Nutrition (grams; sodium in mg) is for one serving of
    serving_size x serving_unit, e.g. serving_size=1, serving_unit="bar".
    Log it afterwards with fitness_log_custom.
    """

    def op(store, client):
        return custom_writes.create_food(
            client,
            description,
            calories=calories,
            protein=protein,
            carbs=carbs,
            fat=fat,
            brand=brand,
            serving_size=serving_size,
            serving_unit=serving_unit,
            fiber=fiber,
            sugar=sugar,
            sodium=sodium,
            saturated_fat=saturated_fat,
        )

    return {"ok": True, **await with_session(ctx, op)}


@mcp.tool()
async def fitness_create_meal(
    name: str, meal: str = "breakfast", date: str | None = None, ctx: Context = None
) -> dict:
    """Save what is logged in one meal of one day as a named saved meal (the
    website's "Remember Meal"), so it can be logged again in one step with
    fitness_log_custom.

    Log the foods first (fitness_log_food / fitness_log_custom), then call
    this. meal: breakfast|lunch|dinner|snacks or any meal name on the
    account. date: YYYY-MM-DD (default: today). Refuses a name that already
    exists.
    """
    day = parse_day(date)

    def op(store, client):
        return custom_writes.create_meal(client, name, day, meal)

    return {"ok": True, **await with_session(ctx, op)}


@mcp.tool()
async def fitness_create_recipe(
    name: str, servings: float, ingredients: list[dict], ctx: Context = None
) -> dict:
    """Create a private recipe in MyFitnessPal, which computes its nutrition
    from the ingredients.

    servings: how many servings the recipe makes.
    ingredients: [{external_id, quantity, serving}], one per ingredient.
    external_id comes from a fitness_search_food candidate (search first);
    serving names one of that food's serving sizes (e.g. "cup"; default: its
    first) and quantity is how many of it go in. Refuses a name that already
    exists. Log it afterwards with fitness_log_custom.
    """

    def op(store, client):
        return custom_writes.create_recipe(client, name, servings, ingredients)

    return {"ok": True, **await with_session(ctx, op)}


@mcp.tool()
async def fitness_delete_custom(name: str, kind: str, ctx: Context = None) -> dict:
    """Delete one of your custom foods, saved meals or recipes. This cannot be
    undone.

    name: the item's exact name as fitness_list_custom shows it (no partial
    matches). kind: food | meal | recipe.
    """

    def op(store, client):
        return custom_writes.delete_custom(client, name, kind)

    return {"ok": True, **await with_session(ctx, op)}


@mcp.tool()
def fitness_list_food_pins() -> dict:
    """List remembered food choices (query → food and serving) that
    fitness_log_food reuses. Stored locally only."""
    return {"pins": get_store().pins()}


@mcp.tool()
def fitness_clear_food_pin(query: str | None = None, clear_all: bool = False) -> dict:
    """Forget a remembered food choice for `query`, or every choice with
    clear_all=True, so the next log of that query drafts options again."""
    store = get_store()
    if clear_all:
        return {"cleared": store.clear_pins()}
    if not query:
        raise ValueError("pass query, or clear_all=True")
    return {"cleared": int(store.clear_pin(query))}


@mcp.tool()
async def fitness_delete_food(
    query: str, meal: str | None = None, date: str | None = None, ctx: Context = None
) -> dict:
    """Remove a food from the MyFitnessPal diary by name match.

    query: text matched against logged entry names (e.g. "banana"). If this
    matches nothing, the error lists what's actually logged that day/meal — use
    that to retry with a better query. If it matches more than one entry (and
    none is an exact name match), the error lists the candidates; narrow `query`
    to pick one.
    meal: optional breakfast|lunch|dinner|snacks or any meal name on the
    account, to disambiguate duplicates. date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        return diary.delete_food(client, day, query, meal)

    result = await with_session(ctx, op)
    return {"ok": True, **result, **await refresh_after_write(ctx, day)}


@mcp.tool()
async def fitness_modify_food(
    query: str,
    new_query: str | None = None,
    meal: str = "breakfast",
    quantity: float | None = None,
    date: str | None = None,
    draft_id: str | None = None,
    option: int | None = None,
    serving: int | None = None,
    ctx: Context = None,
) -> dict:
    """Replace a MyFitnessPal diary entry: deletes the match, then adds a food.

    query: the existing entry to replace (name match) within `meal`. If this
    matches nothing, the error lists what's actually logged that day/meal — use
    that to retry with a better query. If it matches more than one entry (and
    none is an exact name match), the error lists the candidates; narrow `query`
    to pick one.
    new_query: the food to add instead; omit to re-add `query` (e.g. to change
    quantity). The replacement is chosen like fitness_log_food's: a pinned
    food or a single exact-name match is used directly; otherwise nothing is
    changed and a draft comes back (needs_choice=true) — call again with the
    same query plus draft_id + option (and optionally serving).
    meal: breakfast|lunch|dinner|snacks or any meal name on the account,
    used for both the delete and the add. quantity: servings (default 1, or
    the draft's). date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        return food_logging.modify_food(
            client,
            store,
            day,
            meal,
            query,
            new_query,
            quantity,
            draft_id=draft_id,
            option=option,
            serving=serving,
        )

    result = await with_session(ctx, op)
    if result.get("needs_choice"):
        return {"ok": True, **result}
    return {"ok": True, **result, **await refresh_after_write(ctx, day)}


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

    def op(store, client):
        result = diary.set_weight(client, day, weight)
        store.upsert_nutrition(day.isoformat(), weight=result["weight"])
        return {"ok": True, **result}

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_log_water(
    amount: float,
    unit: str = "cup",
    replace: bool = False,
    date: str | None = None,
    ctx: Context = None,
) -> dict:
    """Log water to the real MyFitnessPal water tracker.

    amount: quantity to add to the day's total. unit: cup | fl_oz | ml | l
    (common spellings like "cups", "oz", "fl oz", "milliliters", "litres" are
    accepted). replace: set the day's total to `amount` instead of adding —
    use it to correct a mislogged total (amount=0 clears the day).
    Returns previous_ml; to undo a mistake, call again with
    amount=previous_ml, unit="ml", replace=True.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        result = diary.log_water(client, day, amount, unit, replace=replace)
        store.upsert_nutrition(day.isoformat(), water_ml=result["water_ml"])
        return {"ok": True, **result}

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_get_exercise(date: str | None = None, ctx: Context = None) -> dict:
    """Read the MyFitnessPal exercise diary (cardio + strength) for a day.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        return diary.get_exercise(client, day)

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_get_exercise_entries(
    date: str | None = None, ctx: Context = None
) -> dict:
    """List a day's cardio and strength exercise-diary entries.

    Each entry has its section, name, and that section's columns (cardio:
    minutes, calories_burned; strength: sets, reps_set, weight_set). entry_id
    is informational only — fitness_delete_exercise deletes by name match.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        doc, _ = diary.exercise_page(client, day)
        return {"day": day.isoformat(), "entries": diary.exercise_entries(doc)}

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_delete_exercise(
    query: str,
    date: str | None = None,
    all_matches: bool = False,
    ctx: Context = None,
) -> dict:
    """Remove exercise-diary entries by name match (case-insensitive).

    query: text matched against logged exercise names. By default deletes
    exactly one entry, like fitness_delete_food: an exact name match wins;
    otherwise several matches raise an error listing the candidates, and no
    match raises an error listing what's actually logged.
    all_matches: delete every entry whose name contains `query` — for
    cleaning up syncs that log one workout as several same-named rows
    (e.g. Garmin's "Aerobics, general"). Check fitness_get_exercise_entries
    first; a short query can match many entries.
    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        return diary.delete_exercise(client, day, query, all_matches=all_matches)

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_get_note(date: str | None = None, ctx: Context = None) -> dict:
    """Read the MyFitnessPal daily diary note (the free-text 'Notes' box at the
    bottom of the day) straight from your account.

    date: YYYY-MM-DD (default: today).
    """
    day = parse_day(date)

    def op(store, client):
        body = diary.get_note(client, day)
        store.set_note(day.isoformat(), body)
        return {"day": day.isoformat(), "note": body}

    return await with_session(ctx, op)


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

    def op(store, client):
        result = diary.push_note(client, day, text, append=append)
        store.set_note(day.isoformat(), result["note"])
        return {"ok": True, **result}

    return await with_session(ctx, op)


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
    trend_column(metric)
    start_day, end_day = parse_range(start, end)

    def op(store, client):
        sync.poll(store, client)
        return {
            "metric": metric,
            "points": store.trend(metric, start_day.isoformat(), end_day.isoformat()),
        }

    return await with_session(ctx, op)


@mcp.tool()
async def fitness_bulk_export(
    start: str | None = None,
    end: str | None = None,
    sync_first: bool = False,
    ctx: Context = None,
) -> dict:
    """Export a whole date range at once for analysis: per-day nutrition
    totals, food entries with macros, the MyFitnessPal daily note, and local
    feel notes. Read-only.

    start/end: YYYY-MM-DD (default: last 30 days ending today).
    sync_first: gap-fill from MyFitnessPal before exporting. Off by default so
    large historical exports stay fast on cached data.
    """
    start_day, end_day = parse_range(start, end)

    def op():
        store = get_store()
        if sync_first:
            span = (end_day - start_day).days + 1
            sync.poll(store, mfp_client.get_client(), days=span, force=True)
        days = store.export_range(start_day.isoformat(), end_day.isoformat())
        return {
            "start": start_day.isoformat(),
            "end": end_day.isoformat(),
            "count": len(days),
            "days": days,
        }

    return await run_with_refresh(ctx, op)
