"""Reads and logs your own MyFitnessPal items: custom foods ("My Foods"), saved
meals and recipes.

Each item's loggable food_id + weight_id pairs come from the legacy "Add Food
to Diary" tabs (/food/load_my_foods, load_meals, load_recipes). Logging one of
those rows goes through /food/add like any food, and a saved meal expands into
its ingredients, as it does on the website. Nutrition and ingredients come from
the endpoints the current web app reads: /api/services/users/foods/mine and
meals/mine (session cookie) and v2 /recipes (Bearer token).
"""

import logging
import time
from datetime import date
from urllib import parse

from lxml import html as lh

from . import diary, food_logging
from .mfp_client import is_auth_error

logger = logging.getLogger(__name__)

KINDS = {"foods": "food", "meals": "meal", "recipes": "recipe"}
TABS = {"foods": "my_foods", "meals": "meals", "recipes": "recipes"}

# v2 /recipes answers about one GET in ten with an HTTP 400 that succeeds when
# repeated (seen against a real account, 2026-10). Only reads retry.
RETRY_STATUSES = (400, 429, 500, 502, 503, 504)
MAX_RECIPE_PAGES = 50
LISTED_NAMES_LIMIT = 15


class NoSuchItem(diary.DiaryLookupError):
    pass


class NotLoggedIn(RuntimeError):
    """The message says "not logged in" on purpose: is_auth_error matches it, so
    the server refreshes the session and retries once."""


def clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def web_url(client, path: str) -> str:
    return parse.urljoin(client.BASE_URL_SECURE, path)


def v2_url(client, path: str) -> str:
    return parse.urljoin(client.BASE_API_URL, f"v2/{path}")


def json_headers(client) -> dict:
    return diary.api_headers(
        client, {"Accept": "application/json", "Content-Type": "application/json"}
    )


def get_with_retry(client, url: str, *, params=None, headers=None, attempts=3):
    for attempt in range(attempts):
        resp = client.session.get(url, params=params, headers=headers)
        if resp.status_code not in RETRY_STATUSES or attempt == attempts - 1:
            break
        time.sleep(0.5 * (attempt + 1))
    resp.raise_for_status()
    return resp


def _next_page_url(resp) -> str | None:
    link = (resp.headers or {}).get("link") or ""
    for part in link.split(","):
        if 'rel="next"' in part or "rel=next" in part:
            return part.split(";")[0].strip(" <>")
    return None


def all_recipes(client) -> list[dict]:
    """v2 /recipes pages 10 at a time, whatever the limit, through a
    `Link: <...>; rel="next"` header."""
    url, params, recipes = v2_url(client, "recipes"), {"limit": 100}, []
    for _ in range(MAX_RECIPE_PAGES):
        resp = get_with_retry(client, url, params=params, headers=json_headers(client))
        recipes.extend(resp.json().get("items") or [])
        url, params = _next_page_url(resp), None
        if url is None:
            break
    return recipes


def saved_meals(client) -> list[dict]:
    resp = client.session.get(
        web_url(client, "api/services/users/meals/mine"),
        params={"limit": 100, "search": ""},
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    return resp.json() or []


def kinds(kind: str) -> list[str]:
    if kind == "all":
        return list(KINDS)
    plural = kind if kind in KINDS else f"{kind}s"
    if plural not in KINDS:
        raise ValueError(f"kind must be one of: all, {', '.join(KINDS)}")
    return [plural]


def macros(contents: dict, divisor: float = 1.0) -> dict:
    def per(value):
        return None if value is None else value / divisor

    energy = contents.get("energy") or {}
    return {
        "calories": per(energy.get("value")),
        "protein": per(contents.get("protein")),
        "carbs": per(contents.get("carbohydrates")),
        "fat": per(contents.get("fat")),
    }


def _tab_row(tr, kind: str) -> dict | None:
    food_id = tr.xpath(".//input[contains(@name, '[food_id]')]/@value")
    cells = tr.xpath("./td")
    servings = [
        {"weight_id": option.get("value"), "serving": clean(option.text_content())}
        for option in tr.xpath(".//select[contains(@name, '[weight_id]')]/option")
        if option.get("value")
    ]
    if not food_id or len(cells) < 2 or not servings:
        return None
    return {
        "kind": KINDS[kind],
        "name": clean(cells[1].text_content()),
        "food_id": food_id[0],
        "servings": servings,
    }


def tab_rows(client, kind: str) -> list[dict]:
    # A POST, or a request MFP won't serve, is answered with a 302 to the login
    # page. Following it yields a page with no rows, which reads as "no items".
    resp = client.session.get(
        web_url(client, f"food/load_{TABS[kind]}"),
        params={"meal": "0", "page": "1"},
        headers=diary.api_headers(client),
        allow_redirects=False,
    )
    if 300 <= resp.status_code < 400:
        where = (resp.headers or {}).get("location") or "elsewhere"
        raise NotLoggedIn(
            f"MyFitnessPal sent food/load_{TABS[kind]} to {where}: not logged in "
            "for that page, so your saved items could not be read"
        )
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    rows = lh.fromstring(resp.text).xpath("//tr[contains(@class, 'favorite')]")
    return [row for row in (_tab_row(tr, kind) for tr in rows) if row]


def _food_details(client) -> dict[str, dict]:
    resp = client.session.get(
        web_url(client, "api/services/users/foods/mine"),
        params={"search": ""},
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    details = {}
    for food in resp.json() or []:
        brand = clean(food.get("brand_name") or "")
        name = clean(food.get("description") or "")
        extra = {
            "id": food.get("id"),
            "nutrition": macros(food.get("nutritional_contents") or {}),
        }
        details[name.lower()] = extra
        if brand:
            details[f"{brand} - {name}".lower()] = extra
    return details


def _meal_details(client) -> dict[str, dict]:
    details = {}
    for meal in saved_meals(client):
        ingredients = [
            {
                "name": clean(food.get("description") or ""),
                **{
                    key: food.get(key)
                    for key in ("calories", "protein", "carbs", "fat")
                },
            }
            for food in meal.get("foods") or []
        ]
        totals = {
            key: sum(food.get(key) or 0 for food in ingredients)
            for key in ("calories", "protein", "carbs", "fat")
        }
        details[clean(meal.get("description") or "").lower()] = {
            "meal_id": meal.get("meal_id"),
            "ingredients": ingredients,
            "nutrition": totals,
        }
    return details


def _recipe_details(client) -> dict[str, dict]:
    details = {}
    for recipe in all_recipes(client):
        servings = recipe.get("servings") or 1.0
        details[clean(recipe.get("name") or "").lower()] = {
            "recipe_id": recipe.get("id"),
            "recipe_servings": servings,
            "nutrition": macros(recipe.get("nutritional_contents") or {}, servings),
        }
    return details


DETAIL_READERS = {
    "foods": _food_details,
    "meals": _meal_details,
    "recipes": _recipe_details,
}
EMPTY_DETAILS = {
    "foods": {"id": None, "nutrition": None},
    "meals": {"meal_id": None, "ingredients": None, "nutrition": None},
    "recipes": {"recipe_id": None, "recipe_servings": None, "nutrition": None},
}


def details(client, kind: str) -> tuple[dict[str, dict], str | None]:
    """Extra fields per lowercase item name, and a note when they couldn't be
    read. Names and ids come from the tabs, so listing and logging still work
    without them."""
    try:
        return DETAIL_READERS[kind](client), None
    except Exception as exc:
        if is_auth_error(exc):
            raise
        logger.warning("reading %s details failed: %s", kind, exc)
        return {}, f"{kind} details unavailable ({exc}); names and ids still work"


def items_with_details(
    client, kind: str, rows: list[dict]
) -> tuple[list[dict], str | None]:
    extra, problem = details(client, kind) if rows else ({}, None)
    items = [
        {**row, **extra.get(row["name"].lower(), EMPTY_DETAILS[kind])} for row in rows
    ]
    return items, problem


def list_custom(client, kind: str = "all", query: str | None = None) -> dict:
    needle = (query or "").lower()
    items, warnings = [], []
    for plural in kinds(kind):
        rows = [
            row for row in tab_rows(client, plural) if needle in row["name"].lower()
        ]
        kind_items, problem = items_with_details(client, plural, rows)
        items.extend(kind_items)
        if problem:
            warnings.append(problem)
    return {"items": items, **({"warnings": warnings} if warnings else {})}


def _listed_names(pool: list[dict]) -> str:
    listed = "; ".join(repr(item["name"]) for item in pool[:LISTED_NAMES_LIMIT])
    if len(pool) > LISTED_NAMES_LIMIT:
        listed += (
            f"; … {len(pool) - LISTED_NAMES_LIMIT} more "
            "(narrow with fitness_list_custom query=)"
        )
    return listed or "(none)"


def pick(items: list[dict], name: str, *, exact_only: bool = False) -> dict:
    wanted = clean(name).lower()
    exact = [item for item in items if item["name"].lower() == wanted]
    if len(exact) == 1:
        return exact[0]
    partial = [] if exact_only else [i for i in items if wanted in i["name"].lower()]
    if len(partial) == 1:
        return partial[0]
    if exact or partial:
        raise NoSuchItem(
            f"{name!r} matches several items: {_listed_names(exact or partial)}. "
            "Use one exact name."
        )
    needs = "an exact name" if exact_only else "a name"
    raise NoSuchItem(
        f"nothing matches {name!r}; give {needs} from: {_listed_names(items)}"
    )


def _chosen_serving(item: dict, serving: str | None) -> dict:
    options = item["servings"]
    if serving is None:
        return options[0]
    wanted = serving.lower()
    exact = [option for option in options if option["serving"].lower() == wanted]
    partial = [option for option in options if wanted in option["serving"].lower()]
    if not (exact or partial):
        choices = "; ".join(option["serving"] for option in options)
        raise NoSuchItem(f"{item['name']!r} has no serving {serving!r}: {choices}")
    return (exact or partial)[0]


def log_custom(
    client,
    day: date,
    meal: str,
    name: str,
    *,
    kind: str = "all",
    quantity: float = 1.0,
    serving: str | None = None,
) -> dict:
    item = pick(list_custom(client, kind)["items"], name)
    chosen = _chosen_serving(item, serving)
    food = {
        "food_id": item["food_id"],
        "weight_id": chosen["weight_id"],
        "name": item["name"],
        "serving": chosen["serving"],
    }
    result = food_logging.log_exact(client, food, day, meal, quantity)
    return {**result, "kind": item["kind"]}
