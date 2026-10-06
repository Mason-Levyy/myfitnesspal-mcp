import asyncio
import datetime

import pytest
from conftest import FIXTURES, FakeResponse, diary_after_add

from myfitnesspal_mcp import custom_items, diary, mfp_client, server
from myfitnesspal_mcp.store import Store

TODAY = datetime.date(2026, 7, 8)

MY_FOODS_API = [
    {
        "id": "5001",
        "version": "5001",
        "brand_name": "Corner Cafe",
        "description": "Veggie Bowl",
        "serving_sizes": [{"value": 1, "unit": "bowl", "nutrition_multiplier": 1}],
        "nutritional_contents": {
            "energy": {"unit": "calories", "value": 520},
            "protein": 18,
            "carbohydrates": 70,
            "fat": 19,
        },
    }
]
MEALS_API = [
    {
        "meal_id": 42,
        "description": "Morning smoothie",
        "foods": [
            {"description": "Whey powder, 1 scoop", "calories": 100, "protein": 25},
            {"description": "Berries, 0.5 cup", "calories": 35, "protein": 0},
        ],
    }
]
CHILI = {
    "id": "7001",
    "name": "Turkey Chili",
    "servings": 4.0,
    "nutritional_contents": {
        "energy": {"unit": "calories", "value": 1600.0},
        "protein": 160.0,
        "carbohydrates": 120.0,
        "fat": 40.0,
    },
}
RECIPES_API = {"items": [CHILI]}
SMOOTHIE_ROWS = (
    '<tr><td><a data-food-entry-id="smoothie-a{n}">Whey powder, 1 scoop</a></td></tr>'
    '<tr><td><a data-food-entry-id="smoothie-b{n}">Berries, 0.5 cup</a></td></tr>'
)


def fixture(name):
    return FakeResponse(text=(FIXTURES / name).read_text())


def food_adds(client):
    return [c for c in client.session.calls if c[0] == "POST" and "food/add" in c[1]]


@pytest.fixture
def library(client):
    session = client.session
    session.route("GET", "food/load_my_foods", fixture("favorites_my_foods.html"))
    session.route("GET", "food/load_meals", fixture("favorites_meals.html"))
    session.route("GET", "food/load_recipes", fixture("favorites_recipes.html"))
    session.route(
        "GET", "api/services/users/foods/mine", FakeResponse(json_data=MY_FOODS_API)
    )
    session.route(
        "GET", "api/services/users/meals/mine", FakeResponse(json_data=MEALS_API)
    )
    session.route("GET", "v2/recipes", FakeResponse(json_data=RECIPES_API))
    return client


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(custom_items.time, "sleep", lambda seconds: None)


def test_list_custom_reads_all_three_kinds_with_details(library):
    items = custom_items.list_custom(library)["items"]
    by_name = {item["name"]: item for item in items}
    assert [item["kind"] for item in items] == [
        "food",
        "food",
        "meal",
        "meal",
        "recipe",
    ]

    bowl = by_name["Corner Cafe - Veggie Bowl"]
    assert bowl["food_id"] == "701"
    assert bowl["servings"] == [
        {"weight_id": "7011", "serving": "1 bowl"},
        {"weight_id": "7012", "serving": "100 g"},
    ]
    assert bowl["nutrition"] == {"calories": 520, "protein": 18, "carbs": 70, "fat": 19}

    smoothie = by_name["Morning smoothie"]
    assert smoothie["meal_id"] == 42
    assert [food["name"] for food in smoothie["ingredients"]] == [
        "Whey powder, 1 scoop",
        "Berries, 0.5 cup",
    ]
    assert smoothie["nutrition"]["calories"] == 135

    chili = by_name["Turkey Chili"]
    assert chili["recipe_id"] == "7001"
    assert chili["recipe_servings"] == 4.0
    assert chili["nutrition"] == {
        "calories": 400.0,
        "protein": 40.0,
        "carbs": 30.0,
        "fat": 10.0,
    }


def test_list_custom_filters_by_kind_and_query(library):
    items = custom_items.list_custom(library, kind="meals", query="SMOOTHIE")["items"]
    assert [item["name"] for item in items] == [
        "Morning smoothie",
        "Smoothie, large",
    ]
    tabs = [(m, url) for m, url, _ in library.session.calls if "food/load_" in url]
    assert tabs == [("GET", "https://www.myfitnesspal.com/food/load_meals")]
    assert not [call for call in library.session.calls if call[0] == "POST"]


def test_list_custom_rejects_unknown_kind(library):
    with pytest.raises(ValueError, match="kind must be one of"):
        custom_items.list_custom(library, kind="snacks")


def test_list_custom_reports_a_login_redirect_instead_of_no_items(library):
    library.session.route(
        "GET",
        "food/load_meals",
        FakeResponse(
            status_code=302,
            headers={"location": "https://www.myfitnesspal.com/account/login"},
        ),
    )
    with pytest.raises(custom_items.NotLoggedIn, match="not logged in") as raised:
        custom_items.list_custom(library, kind="meals")
    assert mfp_client.is_auth_error(raised.value)


def test_tab_requests_never_follow_redirects(library):
    custom_items.list_custom(library, kind="foods")
    tab_kwargs = [kw for m, url, kw in library.session.calls if "food/load_" in url]
    assert tab_kwargs[0]["allow_redirects"] is False


def test_list_custom_survives_a_failing_detail_api(library, make_response, caplog):
    library.session.route(
        "GET", "api/services/users/meals/mine", make_response(status_code=500)
    )
    listing = custom_items.list_custom(library, kind="meals")
    smoothie = listing["items"][0]
    assert smoothie["food_id"] == "801"
    assert smoothie["ingredients"] is None
    assert "meals details unavailable" in listing["warnings"][0]
    assert "reading meals details failed" in caplog.text


def test_list_custom_has_no_warnings_when_details_load(library):
    assert "warnings" not in custom_items.list_custom(library, kind="meals")


def test_detail_auth_failures_propagate_for_a_session_refresh(library, make_response):
    library.session.route(
        "GET", "api/services/users/meals/mine", make_response(status_code=401)
    )
    with pytest.raises(Exception) as raised:
        custom_items.list_custom(library, kind="meals")
    assert mfp_client.is_auth_error(raised.value)


def test_recipe_list_retries_a_sporadic_400(library, make_response, no_sleep):
    ok = make_response(json_data=RECIPES_API)
    library.session.route(
        "GET",
        "v2/recipes",
        lambda calls: (
            make_response(status_code=400)
            if sum("v2/recipes" in url for _, url, _ in calls) == 1
            else ok
        ),
    )
    listing = custom_items.list_custom(library, kind="recipes")
    assert listing["items"][0]["recipe_id"] == "7001"
    assert "warnings" not in listing


def test_recipe_list_follows_the_next_page_link(library):
    second = {**CHILI, "id": "7002", "name": "Lentil Soup"}
    next_link = '<https://api.myfitnesspal.com/v2/recipes?page_token=p2>; rel="next"'

    def recipes_page(calls):
        _, url, _ = calls[-1]
        if "page_token=p2" in url:
            return FakeResponse(json_data={"items": [second]})
        return FakeResponse(json_data=RECIPES_API, headers={"link": next_link})

    library.session.route("GET", "v2/recipes", recipes_page)
    recipes = custom_items.all_recipes(library)
    assert [recipe["id"] for recipe in recipes] == ["7001", "7002"]


def test_log_custom_logs_a_saved_meal_and_reports_every_entry(library, diary_html):
    library.session.route(
        "GET", "food/diary?date=", diary_after_add(diary_html, SMOOTHIE_ROWS)
    )
    result = custom_items.log_custom(library, TODAY, "breakfast", "morning smoothie")
    assert result["kind"] == "meal"
    assert result["serving"] == "1 meal"
    assert [entry["name"] for entry in result["added_entries"]] == [
        "Whey powder, 1 scoop",
        "Berries, 0.5 cup",
    ]
    data = food_adds(library)[-1][2]["data"]
    assert data["food_entry[food_id]"] == "801"
    assert data["food_entry[weight_id]"] == "8011"
    assert data["food_entry[meal_id]"] == "0"


def test_log_custom_picks_the_named_serving(library):
    result = custom_items.log_custom(
        library, TODAY, "lunch", "Corner Cafe - Veggie Bowl", quantity=1.5, serving="g"
    )
    data = food_adds(library)[-1][2]["data"]
    assert data["food_entry[weight_id]"] == "7012"
    assert data["food_entry[quantity]"] == "1.5"
    assert result["serving"] == "100 g"


def test_log_custom_unknown_serving_lists_the_choices(library):
    with pytest.raises(custom_items.NoSuchItem, match="1 bowl; 100 g"):
        custom_items.log_custom(
            library, TODAY, "lunch", "Corner Cafe - Veggie Bowl", serving="slice"
        )
    assert not food_adds(library)


def test_log_custom_ambiguous_name_lists_candidates(library):
    with pytest.raises(
        custom_items.NoSuchItem, match="Morning smoothie.*Smoothie, large"
    ):
        custom_items.log_custom(library, TODAY, "breakfast", "smoothie", kind="meals")


def test_log_custom_unknown_name_lists_what_exists(library):
    with pytest.raises(custom_items.NoSuchItem, match="Turkey Chili"):
        custom_items.log_custom(library, TODAY, "dinner", "lasagna", kind="recipes")


def test_no_such_item_never_triggers_a_session_refresh():
    assert not mfp_client.is_auth_error(
        custom_items.NoSuchItem("nothing matches 'session smoothie'")
    )


def test_search_food_returns_the_external_id(client):
    client.food_details[999] = {
        "calories": 105.0,
        "verified": True,
        "nutrition": {},
        "serving_sizes": [],
    }
    candidate = diary.search_food(client, "banana", limit=1)[0]
    assert candidate["external_id"] == "999"


@pytest.fixture
def connected_library(library, tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_store", Store(tmp_path / "cache.db"))
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: library)
    monkeypatch.setattr(server.sync, "refresh_day", lambda store, client, day: None)
    return library


def test_log_custom_tool_logs_and_refreshes_the_day(connected_library):
    result = asyncio.run(
        server.fitness_log_custom(name="Turkey Chili", meal="dinner", date="2026-07-08")
    )
    assert result["ok"] is True
    assert result["kind"] == "recipe"
    assert result["added_entries"]
    assert "refresh_warning" not in result
