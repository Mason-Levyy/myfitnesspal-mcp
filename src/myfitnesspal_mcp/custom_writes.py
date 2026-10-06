"""Creates and deletes your own MyFitnessPal items. Everything created is private.

Custom foods and recipes go through the v2 API. A saved meal is created the way
the website's "Remember Meal" link does it, from what is already logged in one
meal of one day (/meal/new, then /meal/create), and deleted through
/api/services/users/meals/delete with the NextAuth csrf token.
"""

import json
from datetime import date

from lxml import html as lh

from . import diary
from .custom_items import (
    KINDS,
    NoSuchItem,
    all_recipes,
    clean,
    items_with_details,
    json_headers,
    kinds,
    macros,
    pick,
    saved_meals,
    tab_rows,
    v2_url,
    web_url,
)


class InvalidItem(diary.DiaryLookupError):
    pass


def _require_status(resp, endpoint: str, ok: tuple = (200, 201)) -> None:
    if resp.status_code not in ok:
        raise RuntimeError(f"MyFitnessPal {endpoint} returned HTTP {resp.status_code}")


def _food_item(
    description: str,
    nutrition: dict,
    brand: str | None,
    serving_size: float,
    serving_unit: str,
) -> dict:
    item = {
        "description": clean(description),
        "public": False,
        "serving_sizes": [
            {
                "value": serving_size,
                "unit": clean(serving_unit),
                "nutrition_multiplier": 1,
            }
        ],
        "nutritional_contents": nutrition,
    }
    if brand and clean(brand):
        item["brand_name"] = clean(brand)
    return item


def create_food(
    client,
    description: str,
    *,
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
) -> dict:
    if not clean(description or ""):
        raise ValueError("description is required")
    required = {"calories": calories, "protein": protein, "carbs": carbs, "fat": fat}
    for key, value in required.items():
        if value is None or value < 0:
            raise ValueError(f"{key} must be zero or more")
    if not serving_size or serving_size <= 0 or not clean(serving_unit or ""):
        raise ValueError("serving_size must be above zero and serving_unit set")
    optional = {
        "fiber": fiber,
        "sugar": sugar,
        "sodium": sodium,
        "saturated_fat": saturated_fat,
    }
    nutrition = {
        "energy": {"unit": "calories", "value": calories},
        "protein": protein,
        "carbohydrates": carbs,
        "fat": fat,
        **{key: value for key, value in optional.items() if value is not None},
    }
    item = _food_item(description, nutrition, brand, serving_size, serving_unit)
    resp = client.session.post(
        v2_url(client, "foods"),
        data=json.dumps({"items": [item]}),
        headers=json_headers(client),
    )
    _require_status(resp, "/v2/foods")
    made = resp.json()["items"][0]
    brand_name = clean(made.get("brand_name") or "")
    name = clean(made.get("description") or "")
    return {
        "id": made.get("id"),
        "version": made.get("version"),
        "name": f"{brand_name} - {name}" if brand_name else name,
    }


def _saved_meal_named(client, title: str) -> dict | None:
    for meal in saved_meals(client):
        if clean(meal.get("description") or "").lower() == title.lower():
            return meal
    return None


def _meal_form_fields(client, page_url: str) -> dict:
    page = client.session.get(page_url, headers=diary.api_headers(client))
    page.raise_for_status()
    forms = lh.fromstring(page.text).xpath("//form[@action='/meal/create']")
    if not forms:
        raise RuntimeError("couldn't read MyFitnessPal's save-meal form")
    return {
        field.get("name"): field.get("value") or ""
        for field in forms[0].xpath(".//input")
        if field.get("name")
    }


def create_meal(client, name: str, day: date, meal: str) -> dict:
    title = clean(name or "")
    if not title:
        raise ValueError("name is required")
    if _saved_meal_named(client, title):
        raise InvalidItem(f"a saved meal named {title!r} already exists")
    doc, _ = diary.diary_page(client, day)
    meal_id, header = diary.resolve_meal(doc, meal)
    if not diary.find_entries(diary.diary_entries(doc), "", header):
        raise InvalidItem(
            f"nothing is logged in {header} on {day.isoformat()}; log the foods first"
        )
    page_url = web_url(client, f"meal/new?date={day.isoformat()}&meal={meal_id}")
    fields = _meal_form_fields(client, page_url)
    fields["meal[description]"] = title
    resp = client.session.post(
        web_url(client, "meal/create"),
        data=fields,
        headers={"Origin": client.BASE_URL_SECURE.rstrip("/"), "Referer": page_url},
        allow_redirects=False,
    )
    _require_status(resp, "/meal/create", ok=(200, 201, 302, 303))
    saved = _saved_meal_named(client, title)
    if saved is None:
        raise InvalidItem(f"MyFitnessPal did not save the meal {title!r}")
    return {
        "meal_id": saved.get("meal_id"),
        "name": title,
        "ingredients": [
            clean(food.get("description") or "") for food in saved.get("foods") or []
        ],
    }


def _recipe_line(client, ingredient: dict) -> dict:
    external_id = str(ingredient.get("external_id") or "")
    resp = client.session.get(
        v2_url(client, f"foods/{external_id}"),
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
    wanted = (ingredient.get("serving") or "").lower()
    chosen = next((size for size in sizes if wanted in size["unit"].lower()), None)
    if chosen is None:
        choices = "; ".join(f"{size['value']} {size['unit']}" for size in sizes)
        raise InvalidItem(
            f"food {external_id} has no serving {ingredient['serving']!r}: {choices}"
        )
    return {
        "food": {"id": food["id"], "version": food["version"]},
        "serving_size": {
            "value": chosen["value"],
            "unit": chosen["unit"],
            "nutrition_multiplier": chosen["nutrition_multiplier"],
        },
        "servings": ingredient.get("quantity", 1),
    }


def create_recipe(client, name: str, servings: float, ingredients: list[dict]) -> dict:
    title = clean(name or "")
    if not title:
        raise ValueError("name is required")
    if not servings or servings <= 0:
        raise ValueError("servings must be above zero")
    if not ingredients:
        raise ValueError("a recipe needs at least one ingredient")
    if any(
        clean(r.get("name") or "").lower() == title.lower() for r in all_recipes(client)
    ):
        raise InvalidItem(f"a recipe named {title!r} already exists")
    lines = [_recipe_line(client, ingredient) for ingredient in ingredients]
    item = {"name": title, "servings": servings, "public": False, "ingredients": lines}
    resp = client.session.post(
        v2_url(client, "recipes"),
        data=json.dumps({"items": [item]}),
        headers=json_headers(client),
    )
    _require_status(resp, "/v2/recipes")
    made = resp.json()["items"][0]
    made_servings = made.get("servings") or servings
    contents = made.get("nutritional_contents") or {}
    return {
        "recipe_id": made.get("id"),
        "name": clean(made.get("name") or title),
        "servings": made_servings,
        "nutrition_total": macros(contents),
        "nutrition_per_serving": macros(contents, made_servings),
    }


def _delete_request(client, plural: str, item: dict):
    if plural == "foods" and item.get("id"):
        url = v2_url(client, f"foods/{item['id']}")
        return client.session.delete(url, headers=json_headers(client))
    if plural == "recipes" and item.get("recipe_id"):
        url = v2_url(client, f"recipes/{item['recipe_id']}")
        return client.session.delete(url, headers=json_headers(client))
    if plural == "meals" and item.get("meal_id") is not None:
        csrf = client.session.get(web_url(client, "api/auth/csrf")).json()["csrfToken"]
        url = web_url(client, f"api/services/users/meals/delete/{item['meal_id']}")
        return client.session.delete(
            url, headers={"Accept": "application/json", "X-CSRF-Token": csrf}
        )
    raise NoSuchItem(f"couldn't find the MyFitnessPal id of {item['name']!r}")


def delete_custom(client, name: str, kind: str) -> dict:
    plurals = kinds(kind)
    if len(plurals) != 1:
        raise ValueError(f"kind must be one of: {', '.join(KINDS.values())}")
    plural = plurals[0]
    items, problem = items_with_details(client, plural, tab_rows(client, plural))
    if problem:
        raise RuntimeError(f"can't delete safely: {problem}")
    item = pick(items, name, exact_only=True)
    _require_status(_delete_request(client, plural, item), "delete", ok=(200, 204))
    return {"deleted": item["name"], "kind": KINDS[plural]}
