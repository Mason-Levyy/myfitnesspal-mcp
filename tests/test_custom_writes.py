import datetime
import json

import pytest
from conftest import FIXTURES, RECIPES_API, FakeResponse

from myfitnesspal_mcp import custom_items, custom_writes, diary, mfp_client

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


def test_create_food_posts_a_private_v2_food(client):
    client.session.route("POST", "v2/foods", FakeResponse(json_data=OAT_BAR))
    result = custom_writes.create_food(
        client,
        "Oat bar",
        calories=210,
        protein=9,
        carbs=30,
        fat=7,
        brand="Home",
        serving_unit="bar",
        fiber=4,
    )
    assert result == {"id": "6001", "version": "6001", "name": "Home - Oat bar"}
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


def test_create_food_rejects_negative_or_empty_values(client):
    with pytest.raises(ValueError, match="description"):
        custom_writes.create_food(client, "  ", calories=1, protein=0, carbs=0, fat=0)
    with pytest.raises(ValueError, match="calories"):
        custom_writes.create_food(client, "Bar", calories=-5, protein=0, carbs=0, fat=0)
    assert not client.session.calls


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
    result = custom_writes.create_meal(
        meal_saving, "Office breakfast", TODAY, "breakfast"
    )
    assert result["meal_id"] == 77
    _, _, kwargs = posts(meal_saving, "meal/create")[-1]
    assert kwargs["data"]["meal[description]"] == "Office breakfast"
    assert kwargs["data"]["authenticity_token"] == "MEALFORMTOKEN"
    page = [url for m, url, _ in meal_saving.session.calls if "meal/new" in url][-1]
    assert "meal=0" in page and "date=2026-07-08" in page


def test_create_meal_finds_a_custom_meal_by_name(meal_saving, custom_meals_diary_html):
    meal_saving.session.route(
        "GET", "food/diary?date=", FakeResponse(text=custom_meals_diary_html)
    )
    doc = diary.diary_page(meal_saving, TODAY)[0]
    position, header = diary.resolve_meal(doc, diary.meal_headers(doc)[1])
    assert diary.find_entries(diary.diary_entries(doc), "", header)
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


def test_create_recipe_uses_named_servings(recipe_api):
    result = custom_writes.create_recipe(
        recipe_api,
        "Banana bread",
        8,
        [{"external_id": "999", "quantity": 3, "serving": "cup"}],
    )
    assert result["recipe_id"] == "7002"
    assert result["nutrition_per_serving"]["calories"] == 200.0
    _, _, kwargs = posts(recipe_api, "v2/recipes")[-1]
    item = json.loads(kwargs["data"])["items"][0]
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


def test_create_recipe_defaults_to_the_first_serving(recipe_api):
    custom_writes.create_recipe(recipe_api, "Banana bread", 8, [{"external_id": "999"}])
    _, _, kwargs = posts(recipe_api, "v2/recipes")[-1]
    line = json.loads(kwargs["data"])["items"][0]["ingredients"][0]
    assert line["serving_size"]["unit"] == "medium"
    assert line["servings"] == 1


def test_create_recipe_rejects_an_unknown_external_id(recipe_api):
    recipe_api.session.route("GET", "v2/foods/31337", FakeResponse(status_code=404))
    with pytest.raises(custom_writes.InvalidItem, match="fitness_search_food"):
        custom_writes.create_recipe(
            recipe_api, "Mystery", 1, [{"external_id": "31337"}]
        )
    assert not posts(recipe_api, "v2/recipes")


def test_create_recipe_unknown_serving_lists_the_choices(recipe_api):
    with pytest.raises(custom_writes.InvalidItem, match="1.0 medium; 1.0 cup, sliced"):
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


def test_create_recipe_needs_ingredients_and_servings(client):
    with pytest.raises(ValueError, match="at least one ingredient"):
        custom_writes.create_recipe(client, "Empty", 2, [])
    with pytest.raises(ValueError, match="servings"):
        custom_writes.create_recipe(client, "Zero", 0, [{"external_id": "1"}])


def test_delete_custom_deletes_only_an_exact_name(library):
    library.session.route("DELETE", "v2/recipes/7001", FakeResponse(status_code=204))
    assert custom_writes.delete_custom(library, "turkey chili", "recipe") == {
        "deleted": "Turkey Chili",
        "kind": "recipe",
    }
    with pytest.raises(custom_items.NoSuchItem, match="exact name"):
        custom_writes.delete_custom(library, "Turkey", "recipe")


def test_delete_custom_deletes_a_saved_meal_with_the_csrf_token(library):
    library.session.route(
        "GET", "api/auth/csrf", FakeResponse(json_data={"csrfToken": "NEXTAUTH"})
    )
    library.session.route(
        "DELETE", "api/services/users/meals/delete/42", FakeResponse(status_code=200)
    )
    custom_writes.delete_custom(library, "Morning smoothie", "meal")
    _, _, kwargs = [c for c in library.session.calls if c[0] == "DELETE"][-1]
    assert kwargs["headers"]["X-CSRF-Token"] == "NEXTAUTH"


def test_delete_custom_refuses_when_details_fail(library, monkeypatch):
    monkeypatch.setattr(custom_items.time, "sleep", lambda seconds: None)
    library.session.route("GET", "v2/recipes", FakeResponse(status_code=503))
    with pytest.raises(RuntimeError, match="can't delete safely"):
        custom_writes.delete_custom(library, "Turkey Chili", "recipe")
    assert not [c for c in library.session.calls if c[0] == "DELETE"]


def test_delete_custom_needs_one_kind(library):
    with pytest.raises(ValueError, match="kind must be one of: food, meal, recipe"):
        custom_writes.delete_custom(library, "Turkey Chili", "all")
