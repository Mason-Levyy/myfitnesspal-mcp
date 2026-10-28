import asyncio
import datetime
import json

import pytest
from conftest import FIXTURES, RECIPES_API, FakeResponse

from myfitnesspal_mcp import custom_writes, diary, food_search, mfp_client, server

TODAY = datetime.date(2026, 7, 8)

OAT_BAR = {
    "items": [
        {
            "id": "6001",
            "version": "6001",
            "brand_name": "Home",
            "description": "Oat bar",
        }
    ]
}
OAT_BAR_NUTRITION = {"calories": 210, "protein": 9, "carbs": 30, "fat": 7, "fiber": 4}
BANANA_FOOD = {
    "item": {
        "id": "999",
        "version": "12",
        "serving_sizes": [
            {"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0},
            {"value": 1.0, "unit": "cup, sliced", "nutrition_multiplier": 1.5},
        ],
    }
}
BANANA_BREAD = {
    "items": [
        {
            "id": "7002",
            "name": "Banana bread",
            "servings": 8.0,
            "nutritional_contents": {
                "energy": {"unit": "calories", "value": 1600.0},
                "protein": 24.0,
                "carbohydrates": 280.0,
                "fat": 40.0,
            },
        }
    ]
}


def posts(client, fragment):
    return [c for c in client.session.calls if c[0] == "POST" and fragment in c[1]]


def create(client, kind, name, **fields):
    return custom_writes.create_custom(client, kind, name, day=TODAY, **fields)


def test_create_food_posts_a_private_v2_food(client):
    client.session.route("POST", "v2/foods", FakeResponse(json_data=OAT_BAR))
    result = create(
        client,
        "food",
        "Oat bar",
        nutrition=OAT_BAR_NUTRITION,
        brand="Home",
        serving_unit="bar",
    )
    assert result == {
        "kind": "food",
        "id": "6001",
        "version": "6001",
        "name": "Home - Oat bar",
    }
    _, url, kwargs = posts(client, "v2/foods")[-1]
    assert url == "https://api.myfitnesspal.com/v2/foods"
    item = json.loads(kwargs["data"])["items"][0]
    assert item["public"] is False
    assert item["brand_name"] == "Home"
    assert item["serving_sizes"] == [
        {"value": 1, "unit": "bar", "nutrition_multiplier": 1}
    ]
    assert item["nutritional_contents"] == {
        "energy": {"unit": "calories", "value": 210},
        "protein": 9,
        "carbohydrates": 30,
        "fat": 7,
        "fiber": 4,
    }
    assert kwargs["headers"]["Authorization"] == "Bearer fake-token"


def test_create_food_rejects_missing_negative_or_unknown_values(client):
    macros = {"calories": 1, "protein": 0, "carbs": 0, "fat": 0}
    with pytest.raises(ValueError, match="name is required"):
        create(client, "food", "  ", nutrition=macros)
    with pytest.raises(ValueError, match="nutrition.calories"):
        create(client, "food", "Bar", nutrition={**macros, "calories": -5})
    with pytest.raises(ValueError, match="nutrition.protein"):
        create(client, "food", "Bar", nutrition={"calories": 1})
    with pytest.raises(ValueError, match="unknown nutrition keys"):
        create(client, "food", "Bar", nutrition={**macros, "kcal": 1})
    assert not client.session.calls


def test_create_refuses_arguments_for_another_kind(client):
    with pytest.raises(ValueError, match="servings don't apply to kind='food'"):
        create(client, "food", "Bar", nutrition=OAT_BAR_NUTRITION, servings=2)
    with pytest.raises(ValueError, match="nutrition don't apply to kind='meal'"):
        create(client, "meal", "Plate", meal="lunch", nutrition=OAT_BAR_NUTRITION)
    assert not client.session.calls


def test_create_needs_one_kind(client):
    with pytest.raises(ValueError, match="kind must be one of: food, meal, recipe"):
        create(client, "all", "Bar")


@pytest.fixture
def meal_saving(client):
    saved = [{"meal_id": 77, "description": "Office breakfast", "foods": []}]

    def meals(calls):
        created = any(m == "POST" and "meal/create" in url for m, url, _ in calls)
        return FakeResponse(json_data=saved if created else [])

    client.session.route("GET", "api/services/users/meals/mine", meals)
    client.session.route(
        "GET", "meal/new", FakeResponse(text=(FIXTURES / "meal_new.html").read_text())
    )
    client.session.route("POST", "meal/create", FakeResponse(status_code=302))
    return client


def test_create_meal_saves_a_diary_meal_and_confirms_it(meal_saving):
    result = create(meal_saving, "meal", "Office breakfast", meal="breakfast")
    assert result["kind"] == "meal"
    assert result["meal_id"] == 77
    _, _, kwargs = posts(meal_saving, "meal/create")[-1]
    assert kwargs["data"]["meal[description]"] == "Office breakfast"
    assert kwargs["data"]["authenticity_token"] == "MEALFORMTOKEN"
    page = [url for m, url, _ in meal_saving.session.calls if "meal/new" in url][-1]
    assert "meal=0" in page and "date=2026-07-08" in page


def test_create_meal_needs_a_diary_meal(meal_saving):
    with pytest.raises(ValueError, match="needs `meal`"):
        create(meal_saving, "meal", "Office breakfast")
    assert not posts(meal_saving, "meal/create")


def test_create_meal_finds_a_custom_meal_by_name(meal_saving, custom_meals_diary_html):
    meal_saving.session.route(
        "GET", "food/diary?date=", FakeResponse(text=custom_meals_diary_html)
    )
    doc = diary.diary_page(meal_saving, TODAY)[0]
    position, header = diary.resolve_meal(doc, diary.meal_headers(doc)[1])
    custom_writes.create_meal(meal_saving, "Office breakfast", TODAY, header)
    page = [url for m, url, _ in meal_saving.session.calls if "meal/new" in url][-1]
    assert f"meal={position}" in page


def test_create_meal_refuses_an_empty_diary_meal(meal_saving):
    with pytest.raises(custom_writes.InvalidItem, match="nothing is logged in Dinner"):
        custom_writes.create_meal(meal_saving, "Dinner plate", TODAY, "dinner")
    assert not posts(meal_saving, "meal/create")


def test_create_meal_refuses_a_duplicate_name(client):
    existing = [{"meal_id": 5, "description": "Office breakfast", "foods": []}]
    client.session.route(
        "GET", "api/services/users/meals/mine", FakeResponse(json_data=existing)
    )
    with pytest.raises(custom_writes.InvalidItem, match="already exists"):
        custom_writes.create_meal(client, "office breakfast", TODAY, "breakfast")


def test_create_meal_error_after_the_post_never_triggers_a_retry(client):
    client.session.route(
        "GET", "api/services/users/meals/mine", FakeResponse(json_data=[])
    )
    client.session.route(
        "GET", "meal/new", FakeResponse(text=(FIXTURES / "meal_new.html").read_text())
    )
    client.session.route("POST", "meal/create", FakeResponse(status_code=302))
    with pytest.raises(custom_writes.InvalidItem, match="did not save") as raised:
        custom_writes.create_meal(client, "Session snack", TODAY, "breakfast")
    assert not mfp_client.is_auth_error(raised.value)


@pytest.fixture
def recipe_api(client):
    client.session.route("GET", "v2/foods/999", FakeResponse(json_data=BANANA_FOOD))
    client.session.route("GET", "v2/recipes", FakeResponse(json_data={"items": []}))
    client.session.route("POST", "v2/recipes", FakeResponse(json_data=BANANA_BREAD))
    return client


def recipe_lines(client):
    _, _, kwargs = posts(client, "v2/recipes")[-1]
    return json.loads(kwargs["data"])["items"][0]


def test_create_recipe_uses_named_servings(recipe_api):
    result = create(
        recipe_api,
        "recipe",
        "Banana bread",
        servings=8,
        ingredients=[{"external_id": "999", "quantity": 3, "serving": "cup"}],
    )
    assert result["kind"] == "recipe"
    assert result["recipe_id"] == "7002"
    assert result["nutrition_per_serving"]["calories"] == 200.0
    item = recipe_lines(recipe_api)
    assert item["name"] == "Banana bread"
    assert item["servings"] == 8
    assert item["public"] is False
    assert item["ingredients"] == [
        {
            "food": {"id": "999", "version": "12"},
            "serving_size": {
                "value": 1.0,
                "unit": "cup, sliced",
                "nutrition_multiplier": 1.5,
            },
            "servings": 3,
        }
    ]


def test_create_recipe_prefers_an_exact_serving(recipe_api):
    custom_writes.create_recipe(
        recipe_api, "Bread", 1, [{"external_id": "999", "serving": "MEDIUM"}]
    )
    line = recipe_lines(recipe_api)["ingredients"][0]
    assert line["serving_size"]["unit"] == "medium"
    assert line["servings"] == 1


def test_create_recipe_needs_a_serving_when_a_food_has_several(recipe_api):
    with pytest.raises(custom_writes.InvalidItem, match="1 medium; 1 cup, sliced"):
        custom_writes.create_recipe(recipe_api, "Bread", 1, [{"external_id": "999"}])
    assert not posts(recipe_api, "v2/recipes")


def test_create_recipe_takes_a_foods_only_serving(recipe_api):
    only = {
        "item": {
            **BANANA_FOOD["item"],
            "serving_sizes": BANANA_FOOD["item"]["serving_sizes"][:1],
        }
    }
    recipe_api.session.route("GET", "v2/foods/999", FakeResponse(json_data=only))
    custom_writes.create_recipe(recipe_api, "Bread", 1, [{"external_id": "999"}])
    assert (
        recipe_lines(recipe_api)["ingredients"][0]["serving_size"]["unit"] == "medium"
    )


def test_create_recipe_rejects_an_unknown_external_id(recipe_api):
    recipe_api.session.route("GET", "v2/foods/31337", FakeResponse(status_code=404))
    with pytest.raises(custom_writes.InvalidItem, match="fitness_search_food"):
        custom_writes.create_recipe(
            recipe_api, "Mystery", 1, [{"external_id": "31337"}]
        )
    assert not posts(recipe_api, "v2/recipes")


def test_create_recipe_unknown_serving_lists_the_choices(recipe_api):
    with pytest.raises(custom_writes.InvalidItem, match="matches no serving"):
        custom_writes.create_recipe(
            recipe_api, "Bread", 1, [{"external_id": "999", "serving": "tablespoon"}]
        )
    assert not posts(recipe_api, "v2/recipes")


def test_create_recipe_refuses_a_duplicate_name(recipe_api):
    recipe_api.session.route("GET", "v2/recipes", FakeResponse(json_data=RECIPES_API))
    with pytest.raises(custom_writes.InvalidItem, match="already exists"):
        custom_writes.create_recipe(
            recipe_api, "turkey chili", 4, [{"external_id": "999"}]
        )


def test_create_recipe_refuses_an_ingredient_quantity_that_is_not_positive(
    recipe_api,
):
    with pytest.raises(ValueError, match="quantity must be above 0"):
        custom_writes.create_recipe(
            recipe_api,
            "Bread",
            1,
            [{"external_id": "999", "serving": "cup", "quantity": 0}],
        )
    assert not posts(recipe_api, "v2/recipes")


def test_create_recipe_needs_ingredients_and_servings(client):
    with pytest.raises(ValueError, match="at least one ingredient"):
        custom_writes.create_recipe(client, "Empty", 2, [])
    with pytest.raises(ValueError, match="servings"):
        custom_writes.create_recipe(client, "Zero", 0, [{"external_id": "1"}])


def test_search_food_returns_the_external_id(client):
    client.food_details[999] = {
        "calories": 105.0,
        "verified": True,
        "nutrition": {},
        "serving_sizes": [],
    }
    candidate = food_search.search_food(client, "banana", limit=1)[0]
    assert candidate["external_id"] == "999"


def test_create_custom_tool_passes_its_arguments(client, monkeypatch):
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)
    client.session.route("POST", "v2/foods", FakeResponse(json_data=OAT_BAR))
    result = asyncio.run(
        server.fitness_create_custom(
            kind="food", name="Oat bar", nutrition=OAT_BAR_NUTRITION, serving_unit="bar"
        )
    )
    assert result["ok"] is True
    assert result["name"] == "Home - Oat bar"
