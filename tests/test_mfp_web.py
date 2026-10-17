import datetime
from urllib import parse

from myfitnesspal_mcp import food_search, mfp_web

TODAY = datetime.date(2026, 10, 17)


def test_site_url_without_query(client):
    assert mfp_web.site_url(client, "food/note") == (
        "https://www.myfitnesspal.com/food/note"
    )


def test_site_url_with_day(client):
    assert mfp_web.site_url(client, "food/note", TODAY) == (
        "https://www.myfitnesspal.com/food/note?date=2026-10-17"
    )


def test_site_url_joins_params_and_day_into_one_query(client):
    url = mfp_web.site_url(client, "food/search", TODAY, params={"page": 1})
    assert url == "https://www.myfitnesspal.com/food/search?page=1&date=2026-10-17"


def test_search_url_matches_the_hand_built_encoding(client):
    query = "egg & cheese 1/2 cup"
    food_search.search_results(client, query)
    _, url, _ = client.session.calls[0]
    assert url == (
        f"https://www.myfitnesspal.com/food/search?search={parse.quote(query)}&page=1"
    )
