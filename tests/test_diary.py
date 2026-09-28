import datetime

import pytest
from lxml import html as lh

from myfitnesspal_mcp import diary, mfp_client

TODAY = datetime.date(2026, 7, 8)


def meal_id(doc, meal):
    return diary.resolve_meal(doc, meal)[0]


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


def test_pair_servings_zips_weight_ids_by_position():
    serving_sizes = [
        {"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0},
        {"value": 1.0, "unit": "large", "nutrition_multiplier": 1.15},
    ]
    assert diary.pair_servings(["10", "20"], serving_sizes) == [
        {"weight_id": "10", "label": "1 medium", "nutrition_multiplier": 1.0},
        {"weight_id": "20", "label": "1 large", "nutrition_multiplier": 1.15},
    ]


def test_pair_servings_refuses_count_mismatch():
    serving_sizes = [{"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0}]
    assert diary.pair_servings(["10", "20"], serving_sizes) == []


def test_pair_servings_skips_malformed_entries_without_shifting_weight_ids():
    serving_sizes = [
        {"value": None, "unit": None, "nutrition_multiplier": 1.0},
        {"value": 1.0, "unit": "large"},
        "not a serving",
        {"value": 2, "unit": None, "nutrition_multiplier": "2.0"},
        {"value": 40, "unit": "g", "nutrition_multiplier": 0.4},
    ]
    assert diary.pair_servings(["10", "20", "30", "40", "50"], serving_sizes) == [
        {"weight_id": "40", "label": "2", "nutrition_multiplier": 2.0},
        {"weight_id": "50", "label": "40 g", "nutrition_multiplier": 0.4},
    ]


def test_food_candidates_survive_one_malformed_food(client):
    client.food_details[999] = {
        "calories": 105.0,
        "verified": True,
        "nutrition": {},
        "serving_sizes": [{"value": None}, {"unit": "slice"}],
    }
    candidates = diary.food_candidates(client, "banana")
    assert [candidate["name"] for candidate in candidates] == [
        "Banana",
        "Banana Bread",
    ]
    assert candidates[0]["servings"] == []


def test_food_candidates_build_ranking_shape(client):
    client.food_details[999] = {
        "calories": 105.0,
        "verified": True,
        "nutrition": {"protein": 1.3, "carbohydrates": 27.0, "fat": 0.4},
        "serving_sizes": [
            {"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0},
            {"value": 118.0, "unit": "g", "nutrition_multiplier": 1.0},
        ],
    }
    banana, banana_bread = diary.food_candidates(client, "banana")
    assert banana["search_rank"] == 0
    assert banana["verified"] is True
    assert banana["nutrition"] == {
        "calories": 105.0,
        "protein": 1.3,
        "carbs": 27.0,
        "fat": 0.4,
    }
    assert [s["weight_id"] for s in banana["servings"]] == ["10", "20"]
    assert banana["servings"][1]["label"] == "118 g"
    assert banana_bread["servings"] == []
    assert banana_bread["default_weight_id"] == "30"
    assert banana_bread["nutrition"] == {"calories": 196.0}


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
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"
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
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    with pytest.raises(diary.UnknownMeal, match="no MyFitnessPal meal named"):
        diary.push_food(client, TODAY, "brunch", "banana")
    assert all("food/add" not in url for _, url, _ in client.session.calls)


def test_push_food_default_keyword_reaches_renamed_first_meal(
    client, custom_meals_diary_html, make_response
):
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    diary.push_food(client, TODAY, "breakfast", "banana")
    _, _, kwargs = client.session.calls[-1]
    assert kwargs["data"]["food_entry[meal_id]"] == "0"


def test_resolve_meal_id_falls_back_to_default_positions(custom_meals_diary_html):
    doc = lh.fromstring(custom_meals_diary_html)
    assert meal_id(doc, "breakfast") == "0"
    assert meal_id(doc, "Lunch") == "1"
    assert meal_id(doc, "dinner") == "2"
    assert meal_id(doc, "snack") == "3"


def test_resolve_meal_id_prefers_label_over_default_position():
    doc = lh.fromstring(
        "<table><tr class='meal_header'><td>Pre-workout</td></tr>"
        "<tr class='meal_header'><td>Breakfast</td></tr></table>"
    )
    assert meal_id(doc, "breakfast") == "1"


def test_resolve_meal_id_default_keyword_beyond_configured_meals():
    doc = lh.fromstring(
        "<table><tr class='meal_header'><td>Only meal</td></tr></table>"
    )
    with pytest.raises(diary.UnknownMeal, match="Only meal"):
        meal_id(doc, "dinner")


def test_delete_food_default_keyword_filters_renamed_meal(
    client, custom_meals_diary_html, make_response
):
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    result = diary.delete_food(client, TODAY, "banana", "breakfast")
    assert result == {
        "removed": "Banana - Fresh, 1 medium",
        "meal": "fruits/veggies/nuts/seeds",
    }
    _, url, _ = client.session.calls[-1]
    assert url.endswith("food/remove/c1")


def test_delete_food_unknown_meal_raises_before_removing(
    client, custom_meals_diary_html, make_response
):
    client.session.route(
        "GET", "food/diary/tester", make_response(text=custom_meals_diary_html)
    )
    with pytest.raises(diary.UnknownMeal):
        diary.delete_food(client, TODAY, "banana", "brunch")
    assert all("food/remove" not in url for _, url, _ in client.session.calls)


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


def test_meal_headers_ignore_nutrient_column_headings(diary_html):
    doc = lh.fromstring(diary_html)
    assert diary.meal_headers(doc) == ["Breakfast", "Lunch", "Dinner", "Snacks"]


def test_push_food_falls_back_to_diary_csrf_when_search_has_none(client, make_response):
    client.session.route(
        "GET",
        "food/search",
        make_response(
            text=client.session.routes[("GET", "food/search")].text.replace(
                'name="csrf-token"', 'name="unrelated"'
            )
        ),
    )
    diary.push_food(client, TODAY, "breakfast", "banana")
    _, _, kwargs = client.session.calls[-1]
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"


BLANK_SECOND_MEAL = (
    "<table>"
    "<tr class='meal_header'><td>Breakfast</td></tr>"
    "<tr><td><a data-food-entry-id='b1'>Eggs</a></td></tr>"
    "<tr class='meal_header'><td> </td></tr>"
    "<tr><td><a data-food-entry-id='u1'>Mystery Bar</a></td></tr>"
    "<tr class='meal_header'><td>Dinner</td></tr>"
    "<tr class='meal_header'><td>Snacks</td></tr>"
    "</table>"
)


def test_blank_meal_header_keeps_its_meal_id_slot():
    doc = lh.fromstring(BLANK_SECOND_MEAL)
    assert diary.meal_headers(doc) == ["Breakfast", "Meal 2", "Dinner", "Snacks"]
    assert meal_id(doc, "snacks") == "3"
    assert meal_id(doc, "dinner") == "2"


def test_blank_meal_header_entries_agree_with_resolved_label():
    doc = lh.fromstring(BLANK_SECOND_MEAL)
    entries = diary.diary_entries(doc)
    assert [(entry["meal"], entry["entry_id"]) for entry in entries] == [
        ("breakfast", "b1"),
        ("meal 2", "u1"),
    ]
    assert diary.resolve_meal(doc, "lunch") == ("1", "Meal 2")


def test_default_keyword_does_not_fall_back_onto_another_default_meal():
    doc = lh.fromstring(
        "<table><tr class='meal_header'><td>Breakfast</td></tr>"
        "<tr class='meal_header'><td>Dinner</td></tr>"
        "<tr class='meal_header'><td>Supper</td></tr>"
        "<tr class='meal_header'><td>Snacks</td></tr></table>"
    )
    with pytest.raises(diary.UnknownMeal, match="'lunch'"):
        diary.resolve_meal(doc, "lunch")
    assert meal_id(doc, "dinner") == "1"


def test_meal_matching_collapses_whitespace_and_nbsp():
    doc = lh.fromstring(
        "<table><tr class='meal_header'><td>Morning\xa0Snack</td></tr></table>"
    )
    assert diary.resolve_meal(doc, "  morning   snack ") == ("0", "Morning Snack")


def test_diary_page_without_meal_sections_is_an_auth_error(client, make_response):
    client.session.route(
        "GET",
        "food/diary/tester",
        make_response(
            text="<html><head><meta name='csrf-token' content='T'></head>"
            "<body><form id='login'></form></body></html>"
        ),
    )
    with pytest.raises(diary.DiarySignedOut) as exc_info:
        diary.push_food(client, TODAY, "breakfast", "banana")
    assert mfp_client.is_auth_error(exc_info.value)
    assert all("food/add" not in url for _, url, _ in client.session.calls)


def test_lookup_errors_quoting_auth_words_are_not_auth_errors():
    doc = lh.fromstring(
        "<table><tr class='meal_header'><td>Training Session</td></tr></table>"
    )
    with pytest.raises(diary.UnknownMeal) as exc_info:
        diary.resolve_meal(doc, "traning")
    assert not mfp_client.is_auth_error(exc_info.value)
    no_match = diary.NoMatchingEntry("no entry 'login cookie' in session")
    assert not mfp_client.is_auth_error(no_match)


def test_resolve_meal_id_matches_default_labels(diary_html):
    doc = lh.fromstring(diary_html)
    assert meal_id(doc, "breakfast") == "0"
    assert meal_id(doc, "SNACKS") == "3"


def test_resolve_meal_id_matches_renamed_and_extra_custom_meals(
    custom_meals_diary_html,
):
    doc = lh.fromstring(custom_meals_diary_html)
    assert meal_id(doc, "Meat") == "2"
    assert meal_id(doc, "snacks/misc") == "4"
    assert meal_id(doc, "Supplements/Sauces/Spreads") == "5"


def test_resolve_meal_id_raises_with_available_meals_when_unmatched(
    custom_meals_diary_html,
):
    doc = lh.fromstring(custom_meals_diary_html)
    with pytest.raises(diary.UnknownMeal) as exc_info:
        meal_id(doc, "brunch")
    message = str(exc_info.value)
    assert "brunch" in message
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
    diary_fetches = [url for _, url, _ in client.session.calls if "food/diary" in url]
    assert len(diary_fetches) == 1


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
