from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent.parent / "fixtures"

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


@pytest.fixture
def recipes_api():
    return {"items": [CHILI]}


@pytest.fixture
def chili():
    return CHILI


@pytest.fixture
def my_foods_tab():
    def tab(*names, first=0):
        rows = "".join(
            f'<tr class="favorite"><td><input name="favorites[{n}][food_id]" '
            f'type="hidden" value="{600 + n}" /></td><td>{name}</td>'
            f'<td><select name="favorites[{n}][weight_id]">'
            f'<option value="{6000 + n}">1 cup</option></select></td></tr>'
            for n, name in enumerate(names, start=first)
        )
        return f"<table><tbody>{rows}</tbody></table>"

    return tab


@pytest.fixture
def paged(make_response):
    def route(*pages):
        def serve(calls):
            page = int(calls[-1][2]["params"]["page"])
            return make_response(text=pages[page - 1] if page <= len(pages) else "")

        return serve

    return route


@pytest.fixture
def my_food():
    def food(food_id, brand, description, calories):
        return {
            "id": food_id,
            "brand_name": brand,
            "description": description,
            "nutritional_contents": {"energy": {"value": calories}},
        }

    return food


@pytest.fixture
def library(client, make_response, paged, recipes_api):
    session = client.session
    for tab in ("my_foods", "meals", "recipes"):
        session.route(
            "GET",
            f"food/load_{tab}",
            paged((FIXTURES / f"favorites_{tab}.html").read_text()),
        )
    session.route(
        "GET", "api/services/users/foods/mine", make_response(json_data=MY_FOODS_API)
    )
    session.route(
        "GET", "api/services/users/meals/mine", make_response(json_data=MEALS_API)
    )
    session.route("GET", "v2/recipes", make_response(json_data=recipes_api))
    return client
