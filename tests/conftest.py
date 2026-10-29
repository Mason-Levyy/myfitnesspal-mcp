from http.cookiejar import Cookie
from pathlib import Path
from types import SimpleNamespace

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.routes = {}
        self.calls = []

    def route(self, method, path_fragment, response):
        self.routes[(method, path_fragment)] = response

    def _match(self, method, url):
        for (m, fragment), response in self.routes.items():
            if m == method and fragment in url:
                # a callable route answers from the calls so far, e.g. a diary page
                # that changes once something was posted to food/add
                return response(self.calls) if callable(response) else response
        raise AssertionError(f"no fake route for {method} {url}")

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._match("GET", url)

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._match("POST", url)


class FakeClient:
    BASE_URL_SECURE = "https://www.myfitnesspal.com/"
    BASE_API_URL = "https://api.myfitnesspal.com/"

    def __init__(self):
        self.session = FakeSession()
        self.access_token = "fake-token"
        self.user_id = "user-1"
        self.effective_username = "tester"
        self.food_details = {}

    def _get_food_item_details(self, mfp_id):
        detail = self.food_details.get(mfp_id)
        if detail is None:
            raise RuntimeError("no details")
        return detail


ADDED_ROW = (
    '<tr><td><a data-food-entry-id="e-new{n}">Added Food, 1 serving</a></td></tr>'
)


def diary_after_add(before_html, new_row=ADDED_ROW):
    """A diary route that gains one `new_row` (before the totals) per POST to
    food/add so far, as MyFitnessPal's diary does. `{n}` in the row numbers it."""

    def respond(calls):
        adds = sum(1 for m, url, _ in calls if m == "POST" and "food/add" in url)
        rows = "".join(new_row.replace("{n}", str(n)) + "\n  " for n in range(adds))
        return FakeResponse(
            text=before_html.replace(
                '<tr class="bottom">', rows + '<tr class="bottom">'
            )
        )

    return respond


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


def my_foods_tab(*names, first=0):
    rows = "".join(
        f'<tr class="favorite"><td><input name="favorites[{n}][food_id]" '
        f'type="hidden" value="{600 + n}" /></td><td>{name}</td>'
        f'<td><select name="favorites[{n}][weight_id]">'
        f'<option value="{6000 + n}">1 cup</option></select></td></tr>'
        for n, name in enumerate(names, start=first)
    )
    return f"<table><tbody>{rows}</tbody></table>"


def paged(*pages):
    """A tab route that serves pages in order by their page param, then empty
    pages, as MFP does past the last one."""

    def serve(calls):
        page = int(calls[-1][2]["params"]["page"])
        return FakeResponse(text=pages[page - 1] if page <= len(pages) else "")

    return serve


def my_food(food_id, brand, description, calories):
    return {
        "id": food_id,
        "brand_name": brand,
        "description": description,
        "nutritional_contents": {"energy": {"value": calories}},
    }


@pytest.fixture
def library(client):
    """The fake client with your own foods, saved meals and recipes."""
    session = client.session
    for tab in ("my_foods", "meals", "recipes"):
        session.route(
            "GET",
            f"food/load_{tab}",
            paged((FIXTURES / f"favorites_{tab}.html").read_text()),
        )
    session.route(
        "GET", "api/services/users/foods/mine", FakeResponse(json_data=MY_FOODS_API)
    )
    session.route(
        "GET", "api/services/users/meals/mine", FakeResponse(json_data=MEALS_API)
    )
    session.route("GET", "v2/recipes", FakeResponse(json_data=RECIPES_API))
    return client


@pytest.fixture
def make_response():
    return FakeResponse


@pytest.fixture
def search_html():
    return (FIXTURES / "search.html").read_text()


@pytest.fixture
def diary_html():
    return (FIXTURES / "diary.html").read_text()


@pytest.fixture
def exercise_html():
    return (FIXTURES / "exercise.html").read_text()


@pytest.fixture
def exercise_client(exercise_html):
    fake = FakeClient()
    fake.session.route("GET", "exercise/diary/tester", FakeResponse(text=exercise_html))
    fake.session.route("POST", "exercise/remove", FakeResponse(status_code=200))
    return fake


@pytest.fixture
def custom_meals_diary_html():
    return (FIXTURES / "diary_custom_meals.html").read_text()


@pytest.fixture
def client(search_html, diary_html):
    fake = FakeClient()
    fake.session.route("GET", "food/search", FakeResponse(text=search_html))
    fake.session.route("GET", "food/diary?date=", diary_after_add(diary_html))
    fake.session.route("POST", "food/add", FakeResponse(status_code=204))
    fake.session.route("POST", "food/remove", FakeResponse(status_code=200))
    fake.session.route(
        "GET", "food/note", FakeResponse(json_data={"item": {"body": "hello world"}})
    )
    fake.session.route("POST", "food/note", FakeResponse(status_code=200))
    return fake


@pytest.fixture
def store(tmp_path):
    from myfitnesspal_mcp.store import Store

    return Store(tmp_path / "test.db")


@pytest.fixture
def jar_client():
    """Builds a client whose cookie jar looks the way curl_cffi leaves it after
    a sign-in: the loaded cookies on .myfitnesspal.com, with MFP's own sets,
    each (name, value, domain, expires), beside them."""
    from myfitnesspal_mcp import mfp_client

    def build(loaded, set_by_mfp=()):
        jar = mfp_client.cookies_to_jar(loaded)
        for name, value, domain, expires in set_by_mfp:
            jar.set_cookie(
                Cookie(
                    version=0,
                    name=name,
                    value=value,
                    port=None,
                    port_specified=False,
                    domain=domain,
                    domain_specified=True,
                    domain_initial_dot=domain.startswith("."),
                    path="/",
                    path_specified=True,
                    secure=True,
                    expires=expires,
                    discard=expires is None,
                    comment=None,
                    comment_url=None,
                    rest={},
                    rfc2109=False,
                )
            )
        return SimpleNamespace(
            session=SimpleNamespace(cookies=SimpleNamespace(jar=jar)),
            loaded_cookies=dict(loaded),
            saved_cookies=dict(loaded),
            session_renewed=True,
            effective_username="tester",
        )

    return build
