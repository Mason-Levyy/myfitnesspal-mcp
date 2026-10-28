"""Creates your own MyFitnessPal items. Everything created is private.

Custom foods and recipes go through the v2 API. A saved meal is created the way
the website's "Remember Meal" link does it, from what is already logged in one
meal of one day (/meal/new, then /meal/create).
"""

import json
from datetime import date

from lxml import html as lh

from . import diary
from .custom_items import (
    all_recipes,
    macros,
    matching,
    saved_meals,
    single_kind,
)
from .entry_matching import DiaryLookupError, collapse_whitespace, find_entries
from .mfp_web import (
    ORIGIN,
    api_headers,
    api_url,
    json_headers,
    require_status,
    site_url,
)

NUTRIENTS = ("calories", "protein", "carbs", "fat")
OPTIONAL_NUTRIENTS = ("fiber", "sugar", "sodium", "saturated_fat")
FIELDS_BY_KIND = {
    "foods": {"nutrition", "brand", "serving_size", "serving_unit"},
    "meals": {"meal", "date"},
    "recipes": {"servings", "ingredients"},
}


class InvalidItem(DiaryLookupError):
    pass


def _food_item(
    name: str, nutrition: dict, brand: str | None, serving_size: float, unit: str
) -> dict:
    item = {
        "description": name,
        "public": False,
        "serving_sizes": [
            {"value": serving_size, "unit": unit, "nutrition_multiplier": 1}
        ],
        "nutritional_contents": nutrition,
    }
    if brand:
        item["brand_name"] = brand
    return item


def _food_nutrition(nutrition: dict | None) -> dict:
    given = nutrition or {}
    unknown = set(given) - set(NUTRIENTS) - set(OPTIONAL_NUTRIENTS)
    if unknown:
        raise ValueError(
            f"unknown nutrition keys {sorted(unknown)}; use "
            f"{', '.join(NUTRIENTS + OPTIONAL_NUTRIENTS)}"
        )
    for key in NUTRIENTS:
        if given.get(key) is None or given[key] < 0:
            raise ValueError(f"nutrition.{key} is required and must be zero or more")
    return {
        "energy": {"unit": "calories", "value": given["calories"]},
        "protein": given["protein"],
        "carbohydrates": given["carbs"],
        "fat": given["fat"],
        **{key: given[key] for key in OPTIONAL_NUTRIENTS if given.get(key) is not None},
    }


def create_food(
    client,
    name: str,
    nutrition: dict | None,
    *,
    brand: str | None = None,
    serving_size: float = 1,
    serving_unit: str = "serving",
) -> dict:
    unit = collapse_whitespace(serving_unit or "")
    if not serving_size or serving_size <= 0 or not unit:
        raise ValueError("serving_size must be above zero and serving_unit set")
    item = _food_item(
        name,
        _food_nutrition(nutrition),
        collapse_whitespace(brand or "") or None,
        serving_size,
        unit,
    )
    resp = client.session.post(
        api_url(client, "foods"),
        data=json.dumps({"items": [item]}),
        headers=json_headers(client),
    )
    require_status(resp, "/v2/foods", (200, 201))
    made = resp.json()["items"][0]
    made_brand = collapse_whitespace(made.get("brand_name") or "")
    made_name = collapse_whitespace(made.get("description") or "")
    return {
        "id": made.get("id"),
        "version": made.get("version"),
        "name": f"{made_brand} - {made_name}" if made_brand else made_name,
    }


def _saved_meal_named(client, title: str) -> dict | None:
    for meal in saved_meals(client):
        if collapse_whitespace(meal.get("description") or "").lower() == title.lower():
            return meal
    return None


def _meal_form_fields(client, page_url: str) -> dict:
    page = client.session.get(page_url, headers=api_headers(client))
    page.raise_for_status()
    forms = lh.fromstring(page.text).xpath("//form[@action='/meal/create']")
    if not forms:
        raise RuntimeError("couldn't read MyFitnessPal's save-meal form")
    return {
        field.get("name"): field.get("value") or ""
        for field in forms[0].xpath(".//input")
        if field.get("name")
    }


def create_meal(client, title: str, day: date, meal: str) -> dict:
    if _saved_meal_named(client, title):
        raise InvalidItem(f"a saved meal named {title!r} already exists")
    doc, _ = diary.diary_page(client, day)
    meal_id, header = diary.resolve_meal(doc, meal)
    if not find_entries(diary.diary_entries(doc), "", header):
        raise InvalidItem(
            f"nothing is logged in {header} on {day.isoformat()}; log the foods first"
        )
    page_url = site_url(
        client, "meal/new", params={"date": day.isoformat(), "meal": meal_id}
    )
    fields = _meal_form_fields(client, page_url)
    fields["meal[description]"] = title
    resp = client.session.post(
        site_url(client, "meal/create"),
        data=fields,
        headers={"Origin": ORIGIN, "Referer": page_url},
        allow_redirects=False,
    )
    require_status(resp, "/meal/create", (200, 201, 302, 303))
    saved = _saved_meal_named(client, title)
    if saved is None:
        raise InvalidItem(f"MyFitnessPal did not save the meal {title!r}")
    return {
        "meal_id": saved.get("meal_id"),
        "name": title,
        "ingredients": [
            collapse_whitespace(food.get("description") or "")
            for food in saved.get("foods") or []
        ],
    }


def _ingredient_serving(external_id: str, sizes: list[dict], wanted) -> dict:
    choices = "; ".join(f"{size['value']:g} {size['unit']}" for size in sizes)
    if not wanted:
        if len(sizes) == 1:
            return sizes[0]
        raise InvalidItem(
            f"food {external_id} has several serving sizes; name one as "
            f"`serving`: {choices}"
        )
    found = matching(sizes, str(wanted), "unit")
    if len(found) == 1:
        return found[0]
    problem = "matches several servings" if found else "matches no serving"
    raise InvalidItem(f"{wanted!r} {problem} of food {external_id}: {choices}")


def _recipe_line(client, ingredient: dict) -> dict:
    external_id = str(ingredient.get("external_id") or "")
    if not external_id:
        raise InvalidItem(
            "each ingredient needs an external_id from a fitness_search_food result"
        )
    resp = client.session.get(
        api_url(client, f"foods/{external_id}"),
        params=[("fields[]", "serving_sizes"), ("fields[]", "nutritional_contents")],
        headers=json_headers(client),
    )
    if resp.status_code in (400, 404):
        raise InvalidItem(
            f"MyFitnessPal has no food with external_id {external_id!r}; take each "
            "ingredient's external_id from a fitness_search_food result"
        )
    resp.raise_for_status()
    food = resp.json()["item"]
    sizes = food.get("serving_sizes") or []
    if not sizes:
        raise InvalidItem(f"food {external_id} has no serving sizes to measure it by")
    chosen = _ingredient_serving(external_id, sizes, ingredient.get("serving"))
    quantity = ingredient.get("quantity", 1)
    if not quantity > 0:
        raise ValueError(
            f"ingredient {external_id} quantity must be above 0, not {quantity}"
        )
    return {
        "food": {"id": food["id"], "version": food["version"]},
        "serving_size": {
            "value": chosen["value"],
            "unit": chosen["unit"],
            "nutrition_multiplier": chosen["nutrition_multiplier"],
        },
        "servings": quantity,
    }


def create_recipe(
    client, title: str, servings: float | None, ingredients: list[dict] | None
) -> dict:
    if not servings or servings <= 0:
        raise ValueError("servings must be above zero")
    if not ingredients:
        raise ValueError("a recipe needs at least one ingredient")
    if any(
        collapse_whitespace(recipe.get("name") or "").lower() == title.lower()
        for recipe in all_recipes(client)
    ):
        raise InvalidItem(f"a recipe named {title!r} already exists")
    lines = [_recipe_line(client, ingredient) for ingredient in ingredients]
    item = {"name": title, "servings": servings, "public": False, "ingredients": lines}
    resp = client.session.post(
        api_url(client, "recipes"),
        data=json.dumps({"items": [item]}),
        headers=json_headers(client),
    )
    require_status(resp, "/v2/recipes", (200, 201))
    made = resp.json()["items"][0]
    made_servings = made.get("servings") or servings
    contents = made.get("nutritional_contents") or {}
    return {
        "recipe_id": made.get("id"),
        "name": collapse_whitespace(made.get("name") or title),
        "servings": made_servings,
        "nutrition_total": macros(contents),
        "nutrition_per_serving": macros(contents, made_servings),
    }


def create_custom(client, kind: str, name: str, *, day: date, **fields) -> dict:
    """`fields` are the tool's kind-specific arguments; any that were given but
    don't apply to `kind` are refused rather than ignored."""
    plural = single_kind(kind)
    given = {key for key, value in fields.items() if value is not None}
    stray = given - FIELDS_BY_KIND[plural]
    if stray:
        raise ValueError(
            f"{', '.join(sorted(stray))} don't apply to kind={kind!r}; it takes "
            f"{', '.join(sorted(FIELDS_BY_KIND[plural]))}"
        )
    title = collapse_whitespace(name or "")
    if not title:
        raise ValueError("name is required")
    if plural == "foods":
        made = create_food(
            client,
            title,
            fields.get("nutrition"),
            brand=fields.get("brand"),
            serving_size=fields.get("serving_size") or 1,
            serving_unit=fields.get("serving_unit") or "serving",
        )
    elif plural == "meals":
        if not fields.get("meal"):
            raise ValueError("kind='meal' needs `meal`: the diary meal to save")
        made = create_meal(client, title, day, fields["meal"])
    else:
        made = create_recipe(
            client, title, fields.get("servings"), fields.get("ingredients")
        )
    return {"kind": plural[:-1], **made}
