from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

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
                return response
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
def client(search_html, diary_html):
    fake = FakeClient()
    fake.session.route("GET", "food/search", FakeResponse(text=search_html))
    fake.session.route("GET", "food/diary/tester", FakeResponse(text=diary_html))
    fake.session.route("POST", "food/add", FakeResponse(status_code=204))
    fake.session.route("POST", "food/remove", FakeResponse(status_code=200))
    return fake


@pytest.fixture
def store(tmp_path):
    from myfitnesspal_mcp.store import Store

    return Store(tmp_path / "test.db")
