import datetime

import pytest
from lxml import html as lh

from myfitnesspal_mcp import diary

TODAY = datetime.date(2026, 7, 8)


def test_food_search_parses_results_and_csrf(client):
    results, csrf = diary.food_search(client, "banana")
    assert csrf == "CSRF123"
    assert [r["food_id"] for r in results] == ["111", "222"]
    first = results[0]
    assert first["weight_id"] == "10"
    assert first["name"] == "Banana"
    assert first["external_id"] == "999"
    assert first["brand"] == "Fresh Fruit"
    assert first["calories"] == 105.0


def test_food_search_degrades_without_external_id(client):
    results, _ = diary.food_search(client, "banana")
    second = results[1]
    assert second["external_id"] is None
    assert second["calories"] == 196.0


def test_search_food_enriches_macros(client):
    client.food_details[999] = {
        "calories": 105.0,
        "verified": True,
        "nutrition": {"protein": 1.3, "carbohydrates": 27.0, "fat": 0.4},
        "serving_sizes": [{"value": 1, "unit": "medium"}],
    }
    candidates = diary.search_food(client, "banana", limit=2)
    assert len(candidates) == 2
    enriched = candidates[0]
    assert enriched["protein"] == 1.3
    assert enriched["carbs"] == 27.0
    assert enriched["serving"] == "1 medium"
    assert enriched["verified"] is True
    degraded = candidates[1]
    assert degraded["protein"] is None
    assert degraded["calories"] == 196.0


def test_search_food_survives_detail_failures(client):
    candidates = diary.search_food(client, "banana", limit=1)
    assert candidates[0]["calories"] == 105.0
    assert candidates[0]["protein"] is None


def test_push_food_logs_top_match(client):
    result = diary.push_food(client, TODAY, "snacks", "banana", quantity=2.0)
    assert result == {"matched": "Banana", "food_id": "111"}
    method, url, kwargs = client.session.calls[-1]
    assert method == "POST"
    assert "food/add" in url
    data = kwargs["data"]
    assert data["food_entry[food_id]"] == "111"
    assert data["food_entry[weight_id]"] == "10"
    assert data["food_entry[meal_id]"] == "3"
    assert data["food_entry[quantity]"] == "2.0"
    assert data["food_entry[date]"] == "2026-07-08"
    assert kwargs["headers"]["X-CSRF-Token"] == "CSRF123"
    assert kwargs["headers"]["Authorization"] == "Bearer fake-token"


def test_push_food_exact_candidate_uses_diary_csrf(client):
    result = diary.push_food(
        client, TODAY, "lunch", "Banana", food_id="777", weight_id="88"
    )
    assert result["food_id"] == "777"
    method, url, kwargs = client.session.calls[-1]
    assert kwargs["data"]["food_entry[food_id]"] == "777"
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"


def test_push_food_no_results(client, make_response):
    client.session.route(
        "GET", "food/search", make_response(text="<html><body></body></html>")
    )
    with pytest.raises(RuntimeError, match="no MyFitnessPal food found"):
        diary.push_food(client, TODAY, "breakfast", "unobtainium")


def test_push_food_resolves_extra_custom_meal_beyond_the_default_four(
    client, custom_meals_diary_html, make_response
):
    """MFP lets an account have up to 6 meal sections (4 default + 2 extra).
    Meals 5 and 6 have no keyword — only their literal, possibly-custom name
    reaches them. This must work identically to how fitness_delete_food /
    fitness_modify_food already resolve custom meal names."""
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    result = diary.push_food(client, TODAY, "Snacks/Misc", "banana")
    assert result == {"matched": "Banana", "food_id": "111"}
    method, url, kwargs = client.session.calls[-1]
    assert method == "POST"
    assert "food/add" in url
    assert kwargs["data"]["food_entry[meal_id]"] == "4"


def test_push_food_resolves_sixth_custom_meal_case_insensitively(
    client, custom_meals_diary_html, make_response
):
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    result = diary.push_food(client, TODAY, "supplements/sauces/spreads", "banana")
    assert result == {"matched": "Banana", "food_id": "111"}
    _, _, kwargs = client.session.calls[-1]
    assert kwargs["data"]["food_entry[meal_id]"] == "5"


def test_push_food_raises_instead_of_silently_defaulting_to_meal_zero(
    client, custom_meals_diary_html, make_response
):
    """A typo or stale keyword must error loudly, not silently log into
    meal_id 0 the way the old hardcoded MEAL_INDEX.get(meal, "0") did."""
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    with pytest.raises(diary.UnknownMeal, match="no MyFitnessPal meal named"):
        diary.push_food(client, TODAY, "breakfast", "banana")
    assert all("food/add" not in url for _, url, _ in client.session.calls)


def test_meal_headers_lists_labels_in_document_order(custom_meals_diary_html):
    doc = lh.fromstring(custom_meals_diary_html)
    assert diary.meal_headers(doc) == [
        "Fruits/Veggies/Nuts/Seeds",
        "Dairy/Eggs",
        "Meat",
        "Grains/Cereal/Breads/Potatoes",
        "Snacks/Misc",
        "Supplements/Sauces/Spreads",
    ]


def test_resolve_meal_id_matches_default_labels(diary_html):
    doc = lh.fromstring(diary_html)
    assert diary.resolve_meal_id(doc, "breakfast") == "0"
    assert diary.resolve_meal_id(doc, "SNACKS") == "3"


def test_resolve_meal_id_matches_renamed_and_extra_custom_meals(
    custom_meals_diary_html,
):
    doc = lh.fromstring(custom_meals_diary_html)
    assert diary.resolve_meal_id(doc, "Meat") == "2"
    assert diary.resolve_meal_id(doc, "snacks/misc") == "4"
    assert diary.resolve_meal_id(doc, "Supplements/Sauces/Spreads") == "5"


def test_resolve_meal_id_raises_with_available_meals_when_unmatched(
    custom_meals_diary_html,
):
    doc = lh.fromstring(custom_meals_diary_html)
    with pytest.raises(diary.UnknownMeal) as exc_info:
        diary.resolve_meal_id(doc, "breakfast")
    message = str(exc_info.value)
    assert "breakfast" in message
    assert "Fruits/Veggies/Nuts/Seeds" in message
    assert "Supplements/Sauces/Spreads" in message


def test_diary_entries_map_meals(client):
    doc, token = diary.diary_page(client, TODAY)
    assert token == "DIARYTOKEN"
    entries = diary.diary_entries(doc)
    assert [(e["meal"], e["entry_id"]) for e in entries] == [
        ("breakfast", "e1"),
        ("breakfast", "e2"),
        ("lunch", "e3"),
    ]


def test_find_entries_scoped_by_meal():
    entries = [
        {"entry_id": "1", "meal": "breakfast", "name": "Banana"},
        {"entry_id": "2", "meal": "lunch", "name": "Banana Bread"},
    ]
    assert [e["entry_id"] for e in diary.find_entries(entries, "banana")] == ["1", "2"]
    assert [e["entry_id"] for e in diary.find_entries(entries, "banana", "lunch")] == [
        "2"
    ]
    assert diary.find_entries(entries, "kale") == []


def test_find_entries_accepts_singular_snack_alias():
    entries = [{"entry_id": "1", "meal": "snacks", "name": "Cherries"}]
    assert [
        e["entry_id"] for e in diary.find_entries(entries, "cherries", "snack")
    ] == ["1"]
    assert [
        e["entry_id"] for e in diary.find_entries(entries, "cherries", "Snack")
    ] == ["1"]


def test_resolve_entry_returns_single_substring_match():
    entries = [{"entry_id": "1", "meal": "breakfast", "name": "Banana"}]
    entry = diary.resolve_entry(entries, "banana", None, TODAY)
    assert entry["entry_id"] == "1"


def test_resolve_entry_exact_match_wins_over_substring():
    entries = [
        {"entry_id": "1", "meal": "breakfast", "name": "Banana"},
        {"entry_id": "2", "meal": "breakfast", "name": "Banana Bread"},
    ]
    entry = diary.resolve_entry(entries, "Banana", None, TODAY)
    assert entry["entry_id"] == "1"


def test_resolve_entry_raises_ambiguous_when_no_exact_match():
    entries = [
        {"entry_id": "1", "meal": "dinner", "name": "Chicken Salad"},
        {"entry_id": "2", "meal": "dinner", "name": "Chicken Soup"},
    ]
    with pytest.raises(diary.AmbiguousEntry) as exc_info:
        diary.resolve_entry(entries, "chicken", None, TODAY)
    message = str(exc_info.value)
    assert "Chicken Salad" in message
    assert "Chicken Soup" in message
    assert exc_info.value.candidates == entries


def test_resolve_entry_no_match_lists_what_is_actually_logged():
    entries = [{"entry_id": "1", "meal": "dinner", "name": "Rice"}]
    with pytest.raises(diary.NoMatchingEntry, match="Rice"):
        diary.resolve_entry(entries, "beef", "dinner", TODAY)


def test_delete_food_removes_match(client):
    result = diary.delete_food(client, TODAY, "coffee")
    assert result == {"removed": "Coffee, 1 cup", "meal": "breakfast"}
    method, url, kwargs = client.session.calls[-1]
    assert "food/remove/e2" in url
    assert kwargs["data"]["_method"] == "delete"
    assert kwargs["data"]["authenticity_token"] == "DIARYTOKEN"


def test_delete_food_no_match(client):
    with pytest.raises(diary.NoMatchingEntry, match="in dinner"):
        diary.delete_food(client, TODAY, "coffee", meal="dinner")


def test_modify_food_deletes_then_adds(client):
    result = diary.modify_food(client, TODAY, "breakfast", "coffee", "banana")
    assert result == {
        "removed": "Coffee, 1 cup",
        "added": "Banana",
        "meal": "breakfast",
    }


def test_get_note_double_unescapes_body(client, make_response):
    client.session.route(
        "GET", "food/note", make_response(json_data={"item": {"body": "a &amp;amp; b"}})
    )
    assert diary.get_note(client, TODAY) == "a & b"
    method, url, kwargs = client.session.calls[-1]
    assert method == "GET"
    assert "food/note?date=2026-07-08" in url


def test_get_note_empty_is_none(client, make_response):
    client.session.route(
        "GET", "food/note", make_response(json_data={"item": {"body": ""}})
    )
    assert diary.get_note(client, TODAY) is None


def test_set_note_posts_form_body_and_csrf(client):
    result = diary.set_note(client, TODAY, "today test\n")
    assert result == {"day": "2026-07-08", "note": "today test\n"}
    method, url, kwargs = client.session.calls[-1]
    assert method == "POST"
    assert "food/note" in url
    assert kwargs["data"] == {"body": "today test\n", "date": "2026-07-08"}
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"
    assert kwargs["headers"]["Content-Type"].startswith(
        "application/x-www-form-urlencoded"
    )


def test_push_note_append_keeps_existing(client, make_response):
    client.session.route(
        "GET", "food/note", make_response(json_data={"item": {"body": "line one"}})
    )
    result = diary.push_note(client, TODAY, "line two", append=True)
    assert result["note"] == "line one\nline two"
    method, url, kwargs = client.session.calls[-1]
    assert kwargs["data"]["body"] == "line one\nline two"


def test_set_weight_posts_v2_items(client, make_response):
    client.session.route(
        "POST",
        "v2/measurements",
        make_response(
            status_code=200,
            json_data={
                "items": [
                    {
                        "type": "Weight",
                        "value": 175.0,
                        "date": "2026-07-08",
                        "unit": "pounds",
                    }
                ]
            },
        ),
    )
    result = diary.set_weight(client, TODAY, 175.0)
    assert result == {"day": "2026-07-08", "weight": 175.0, "unit": "pounds"}
    method, url, kwargs = client.session.calls[-1]
    assert "v2/measurements" in url
    assert kwargs["json"] == {
        "items": [{"type": "Weight", "value": 175.0, "date": "2026-07-08"}]
    }
