import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from lxml import html as lh

from . import food_logging
from .diary import DiarySignedOut
from .entry_matching import (
    DiaryLookupError,
    collapse_whitespace,
    listed_names,
    matching,
)
from .mfp_client import is_auth_error
from .mfp_web import api_headers, api_url, get_with_retry, json_headers, site_url

logger = logging.getLogger(__name__)

MAX_RECIPE_PAGES = 50
MAX_TAB_PAGES = 40
NAME_COLUMN = 1
MACRO_KEYS = ("calories", "protein", "carbs", "fat")
NARROW_HINT = " (narrow with fitness_list_custom query=)"


@dataclass(frozen=True)
class Kind:
    singular: str
    tab: str
    read_details: Callable[[object], list[tuple[str, dict]]]
    empty_details: dict
    create_fields: frozenset[str]
    id_field: str


class NoSuchItem(DiaryLookupError):
    pass


def _next_page_url(resp) -> str | None:
    link = (resp.headers or {}).get("link") or ""
    for part in link.split(","):
        if 'rel="next"' in part or "rel=next" in part:
            return part.split(";")[0].strip(" <>")
    return None


def all_recipes(client) -> list[dict]:
    url, recipes = api_url(client, "recipes"), []
    for _ in range(MAX_RECIPE_PAGES):
        resp = get_with_retry(client, url, headers=json_headers(client))
        recipes.extend(resp.json().get("items") or [])
        url = _next_page_url(resp)
        if url is None:
            break
    return recipes


def _users_mine(client, kind: str, params: dict) -> list[dict]:
    resp = get_with_retry(
        client,
        site_url(client, f"api/services/users/{kind}/mine"),
        params=params,
        headers=api_headers(client, {"Accept": "application/json"}),
    )
    return resp.json() or []


def saved_meals(client) -> list[dict]:
    return _users_mine(client, "meals", {"limit": 100, "search": ""})


def nutrition_per(contents: dict, divisor: float = 1.0) -> dict:
    def per(value):
        return None if value is None else value / divisor

    energy = contents.get("energy") or {}
    return {
        "calories": per(energy.get("value")),
        "protein": per(contents.get("protein")),
        "carbs": per(contents.get("carbohydrates")),
        "fat": per(contents.get("fat")),
    }


def _food_details(client) -> list[tuple[str, dict]]:
    details = []
    for food in _users_mine(client, "foods", {"search": ""}):
        brand = collapse_whitespace(food.get("brand_name") or "")
        name = collapse_whitespace(food.get("description") or "")
        shown = f"{brand} - {name}" if brand else name
        details.append(
            (
                shown,
                {
                    "id": food.get("id"),
                    "nutrition": nutrition_per(food.get("nutritional_contents") or {}),
                },
            )
        )
    return details


def _meal_details(client) -> list[tuple[str, dict]]:
    details = []
    for meal in saved_meals(client):
        ingredients = [
            {
                "name": collapse_whitespace(food.get("description") or ""),
                **{key: food.get(key) for key in MACRO_KEYS},
            }
            for food in meal.get("foods") or []
        ]
        totals = {
            key: sum(food.get(key) or 0 for food in ingredients) for key in MACRO_KEYS
        }
        details.append(
            (
                collapse_whitespace(meal.get("description") or ""),
                {
                    "meal_id": meal.get("meal_id"),
                    "ingredients": ingredients,
                    "nutrition": totals,
                },
            )
        )
    return details


def _recipe_details(client) -> list[tuple[str, dict]]:
    details = []
    for recipe in all_recipes(client):
        servings = recipe.get("servings") or 1.0
        details.append(
            (
                collapse_whitespace(recipe.get("name") or ""),
                {
                    "recipe_id": recipe.get("id"),
                    "recipe_servings": servings,
                    "nutrition": nutrition_per(
                        recipe.get("nutritional_contents") or {}, servings
                    ),
                },
            )
        )
    return details


KINDS = {
    "foods": Kind(
        "food",
        "my_foods",
        _food_details,
        {"id": None, "nutrition": None},
        frozenset({"nutrition", "brand", "serving_size", "serving_unit"}),
        "id",
    ),
    "meals": Kind(
        "meal",
        "meals",
        _meal_details,
        {"meal_id": None, "ingredients": None, "nutrition": None},
        frozenset({"meal", "date"}),
        "meal_id",
    ),
    "recipes": Kind(
        "recipe",
        "recipes",
        _recipe_details,
        {"recipe_id": None, "recipe_servings": None, "nutrition": None},
        frozenset({"servings", "ingredients"}),
        "recipe_id",
    ),
}


def kinds(kind: str) -> list[str]:
    if kind == "all":
        return list(KINDS)
    plural = kind if kind in KINDS else f"{kind}s"
    if plural not in KINDS:
        raise ValueError(f"kind must be one of: all, {', '.join(KINDS)}")
    return [plural]


def single_kind(kind: str) -> str:
    plurals = kinds(kind)
    if len(plurals) != 1:
        raise ValueError("kind must be one of: food, meal, recipe")
    return plurals[0]


def _tab_row(tr, kind: str) -> dict | None:
    food_id = tr.xpath(".//input[contains(@name, '[food_id]')]/@value")
    cells = tr.xpath("./td")
    servings = [
        {
            "weight_id": option.get("value"),
            "serving": collapse_whitespace(option.text_content()),
        }
        for option in tr.xpath(".//select[contains(@name, '[weight_id]')]/option")
        if option.get("value")
    ]
    if not food_id or len(cells) <= NAME_COLUMN or not servings:
        return None
    return {
        "kind": KINDS[kind].singular,
        "name": collapse_whitespace(cells[NAME_COLUMN].text_content()),
        "food_id": food_id[0],
        "servings": servings,
    }


def _tab_page(client, kind: str, page: int) -> list[dict]:
    tab = KINDS[kind].tab
    resp = client.session.get(
        site_url(client, f"food/load_{tab}"),
        params={"meal": "0", "page": str(page)},
        headers=api_headers(client),
        allow_redirects=False,
    )
    if 300 <= resp.status_code < 400:
        raise DiarySignedOut(
            f"MyFitnessPal redirected food/load_{tab} instead of serving it; the "
            "login session has likely expired"
        )
    resp.raise_for_status()
    if not resp.text.strip():
        return []
    rows = lh.fromstring(resp.text).xpath("//tr[contains(@class, 'favorite')]")
    return [row for row in (_tab_row(tr, kind) for tr in rows) if row]


def tab_rows(client, kind: str) -> list[dict]:
    rows, seen = [], set()
    for page in range(1, MAX_TAB_PAGES + 1):
        new = [
            row for row in _tab_page(client, kind, page) if row["food_id"] not in seen
        ]
        if not new:
            break
        seen.update(row["food_id"] for row in new)
        rows.extend(new)
    return rows


def _by_name(kind: str, details: list[tuple[str, dict]]) -> tuple[dict, str | None]:
    by_name, first_spelling, shared = {}, {}, set()
    for name, extra in details:
        key = name.lower()
        if key in by_name:
            shared.add(key)
        else:
            first_spelling[key] = name
        by_name[key] = extra
    if not shared:
        return by_name, None
    for key in shared:
        del by_name[key]
    names = ", ".join(sorted(repr(first_spelling[key]) for key in shared))
    return by_name, (
        f"several {kind} are named {names}, so their details and ids are left "
        "out rather than guessed"
    )


def details(client, kind: str) -> tuple[dict[str, dict], str | None]:
    try:
        read = KINDS[kind].read_details(client)
    except Exception as exc:
        if is_auth_error(exc):
            raise
        logger.warning("reading %s details failed: %s", kind, exc)
        return {}, f"{kind} details unavailable ({exc}); names and ids still work"
    return _by_name(kind, read)


def items_with_details(
    client, kind: str, rows: list[dict]
) -> tuple[list[dict], str | None]:
    extra, problem = details(client, kind) if rows else ({}, None)
    empty = KINDS[kind].empty_details
    items = [{**row, **extra.get(row["name"].lower(), empty)} for row in rows]
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
    return {"items": items, "warnings": warnings}


def pick(items: list[dict], name: str, *, exact_only: bool = False) -> dict:
    if not name.strip():
        raise NoSuchItem(
            f"name is required; pick one from: {listed_names(items, NARROW_HINT)}"
        )
    found = matching(items, name, "name", exact_only=exact_only)
    if len(found) == 1:
        return found[0]
    if found and len({item["name"].lower() for item in found}) == 1:
        raise NoSuchItem(
            f"several items are named {found[0]['name']!r}, so the name can't "
            "pick one. To log one, pass its food_id and weight_id from "
            "fitness_list_custom to fitness_log_food; to delete one, rename or "
            "delete it in the MyFitnessPal app"
        )
    if found:
        raise NoSuchItem(
            f"{name!r} matches several items: {listed_names(found, NARROW_HINT)}. "
            "Use one exact name."
        )
    needs = "an exact name" if exact_only else "a name"
    raise NoSuchItem(
        f"nothing matches {name!r}; give {needs} from: "
        f"{listed_names(items, NARROW_HINT)}"
    )


def chosen_serving(item: dict, serving: str | None) -> dict:
    options = item["servings"]
    if serving is None:
        return options[0]
    found = matching(options, serving, "serving")
    if len(found) == 1:
        return found[0]
    choices = "; ".join(option["serving"] for option in options)
    problem = "matches several servings" if found else "matches no serving"
    raise NoSuchItem(f"{serving!r} {problem} of {item['name']!r}: {choices}")


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
    if quantity <= 0:
        raise ValueError(f"quantity must be above 0, not {quantity}")
    rows = [row for plural in kinds(kind) for row in tab_rows(client, plural)]
    item = pick(rows, name)
    chosen = chosen_serving(item, serving)
    food = {
        "food_id": item["food_id"],
        "weight_id": chosen["weight_id"],
        "name": item["name"],
        "serving": chosen["serving"],
    }
    result = food_logging.log_exact(client, food, day, meal, quantity=quantity)
    return {**result, "kind": item["kind"]}
