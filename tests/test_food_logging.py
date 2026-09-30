import datetime

import pytest

from myfitnesspal_mcp import food_logging
from myfitnesspal_mcp.food_ranking import MacroTargets

TODAY = datetime.date(2026, 7, 8)


@pytest.fixture
def banana_details(client):
    client.food_details[999] = {
        "calories": 105.0,
        "verified": True,
        "nutrition": {"protein": 1.3, "carbohydrates": 27.0, "fat": 0.4},
        "serving_sizes": [
            {"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0},
            {"value": 1.0, "unit": "large", "nutrition_multiplier": 1.15},
        ],
    }
    return client


def _food_adds(client):
    return [call for call in client.session.calls if call[0] == "POST"]


def test_draft_lists_options_with_every_serving(banana_details, store):
    draft = food_logging.draft_food(banana_details, store, "banana", TODAY)
    assert draft["draft_id"]
    banana = draft["options"][0]
    assert banana["name"] == "Banana"
    assert banana["suggested_serving"] == 0
    assert [s["label"] for s in banana["servings"]] == ["1 medium", "1 large"]
    assert banana["servings"][1]["calories"] == pytest.approx(120.75, abs=0.05)
    assert _food_adds(banana_details) == []


def test_draft_targets_pick_the_fitting_serving(banana_details, store):
    draft = food_logging.draft_food(
        banana_details, store, "banana", TODAY, targets=MacroTargets(min_calories=115)
    )
    banana = next(o for o in draft["options"] if o["name"] == "Banana")
    assert banana["suggested_serving"] == 1
    assert banana["fits_targets"] is True


def test_log_from_draft_posts_chosen_serving_and_pins(banana_details, store):
    draft = food_logging.draft_food(banana_details, store, "banana", TODAY)
    result = food_logging.log_from_draft(
        banana_details, store, draft["draft_id"], option=1, serving=1, quantity=2
    )
    assert result["weight_id"] == "20"
    assert result["serving"] == "1 large"
    _, _, kwargs = _food_adds(banana_details)[-1]
    assert kwargs["data"]["food_entry[weight_id]"] == "20"
    assert kwargs["data"]["food_entry[quantity]"] == "2"
    assert kwargs["data"]["food_entry[food_id]"] == "111"
    assert store.pin("banana")["weight_id"] == "20"


def test_log_from_draft_can_skip_pinning(banana_details, store):
    draft = food_logging.draft_food(banana_details, store, "banana", TODAY)
    food_logging.log_from_draft(
        banana_details, store, draft["draft_id"], option=1, pin=False
    )
    assert store.pin("banana") is None


def test_log_from_draft_rejects_bad_choices(banana_details, store):
    draft = food_logging.draft_food(banana_details, store, "banana", TODAY)
    with pytest.raises(ValueError, match="option must be between 1 and"):
        food_logging.log_from_draft(banana_details, store, draft["draft_id"], 9)
    with pytest.raises(ValueError, match="serving must be between 0 and 1"):
        food_logging.log_from_draft(
            banana_details, store, draft["draft_id"], 1, serving=5
        )
    with pytest.raises(food_logging.DraftNotFound, match="expired"):
        food_logging.log_from_draft(banana_details, store, "nope", 1)
    assert _food_adds(banana_details) == []


def test_option_without_servings_uses_default_weight(banana_details, store):
    draft = food_logging.draft_food(banana_details, store, "banana", TODAY)
    bread = next(o for o in draft["options"] if o["name"] == "Banana Bread")
    result = food_logging.log_from_draft(
        banana_details, store, draft["draft_id"], bread["option"]
    )
    assert result["weight_id"] == "30"


def test_unpaired_food_ranks_on_search_listing_calories(client, store):
    draft = food_logging.draft_food(
        client, store, "banana", TODAY, targets=MacroTargets(max_calories=200)
    )
    bread = next(o for o in draft["options"] if o["name"] == "Banana Bread")
    assert bread["fits_targets"] is True
    assert bread["servings"] == [
        {
            "serving": 0,
            "label": "1 slice",
            "calories": 196.0,
            "protein": None,
            "carbs": None,
            "fat": None,
            "fits_targets": True,
        }
    ]


def test_pin_survives_details_failure_on_confirm(client, store):
    store.set_pin("banana", "111", "20", "Banana", "1 large")
    draft = food_logging.draft_food(client, store, "banana", TODAY)
    banana = draft["options"][0]
    assert banana["pinned"] is True
    assert banana["servings"][banana["suggested_serving"]]["label"] == "1 large"
    result = food_logging.log_from_draft(client, store, draft["draft_id"], option=1)
    assert result["weight_id"] == "20"
    assert store.pin("banana")["weight_id"] == "20"


def test_bare_query_uses_pin_without_searching(client, store):
    store.set_pin("banana", "555", "77", "Banana, organic", "1 small")
    result = food_logging.log_by_query(
        client, store, "Banana", TODAY, meal="lunch", quantity=1.0
    )
    assert result["source"] == "pin"
    assert not any("food/search" in url for _, url, _ in client.session.calls)
    _, _, kwargs = _food_adds(client)[-1]
    assert kwargs["data"]["food_entry[food_id]"] == "555"
    assert kwargs["data"]["food_entry[weight_id]"] == "77"
    assert kwargs["data"]["food_entry[meal_id]"] == "1"


def test_bare_query_logs_single_exact_match(banana_details, store):
    result = food_logging.log_by_query(
        banana_details, store, "banana", TODAY, meal="breakfast", quantity=1.0
    )
    assert result["source"] == "exact_match"
    assert result["weight_id"] == "10"
    assert store.pin("banana") is None


def test_bare_exact_match_skips_details_calls(banana_details, store, monkeypatch):
    def details_must_not_run(external_id):
        raise AssertionError("details fetched for an unambiguous exact match")

    monkeypatch.setattr(banana_details, "_get_food_item_details", details_must_not_run)
    result = food_logging.log_by_query(
        banana_details, store, "Banana", TODAY, meal="breakfast", quantity=1.0
    )
    assert result["source"] == "exact_match"
    assert result["serving"] == "1 medium"
    searches = [url for _, url, _ in banana_details.session.calls if "search" in url]
    assert len(searches) == 1


def test_pin_and_exact_paths_report_one_shape(banana_details, store):
    exact = food_logging.log_by_query(
        banana_details, store, "banana", TODAY, meal="breakfast", quantity=1.0
    )
    store.set_pin("banana", "111", "20", "Banana", "1 large")
    pinned = food_logging.log_by_query(
        banana_details, store, "banana", TODAY, meal="breakfast", quantity=1.0
    )
    assert set(exact) == set(pinned)


def test_bare_ambiguous_query_returns_draft_and_logs_nothing(banana_details, store):
    result = food_logging.log_by_query(
        banana_details, store, "bread", TODAY, meal="breakfast", quantity=1.0
    )
    assert result["logged"] is None
    assert result["needs_choice"] is True
    assert result["draft_id"]
    assert _food_adds(banana_details) == []


def test_pinned_food_missing_from_search_still_offered_first(client, store):
    store.set_pin("banana", "555", "77", "Banana, organic", "1 small")
    draft = food_logging.draft_food(client, store, "banana", TODAY)
    first = draft["options"][0]
    assert first["pinned"] is True
    assert first["name"] == "Banana, organic"
    assert first["servings"][0]["label"] == "1 small"


def _posts_to(client, fragment):
    return [call for call in _food_adds(client) if fragment in call[1]]


def test_modify_replaces_with_exact_match_using_one_diary_fetch(banana_details, store):
    result = food_logging.modify_food(
        banana_details, store, TODAY, "breakfast", query="coffee", new_query="banana"
    )
    assert result["removed"] == "Coffee, 1 cup"
    assert result["logged"] == "Banana"
    assert result["source"] == "exact_match"
    assert len(_posts_to(banana_details, "food/remove")) == 1
    assert len(_posts_to(banana_details, "food/add")) == 1
    diary_fetches = [
        url for _, url, _ in banana_details.session.calls if "food/diary" in url
    ]
    assert len(diary_fetches) == 1


def test_modify_uses_pin_for_replacement(client, store):
    store.set_pin("protein shake", "555", "77", "Whey Shake", "1 scoop")
    result = food_logging.modify_food(
        client, store, TODAY, "breakfast", query="coffee", new_query="Protein  Shake"
    )
    assert result["source"] == "pin"
    _, _, kwargs = _posts_to(client, "food/add")[-1]
    assert kwargs["data"]["food_entry[food_id]"] == "555"
    assert kwargs["data"]["food_entry[weight_id]"] == "77"


def test_modify_ambiguous_replacement_deletes_nothing(banana_details, store):
    result = food_logging.modify_food(
        banana_details, store, TODAY, "breakfast", query="coffee", new_query="bread"
    )
    assert result["needs_choice"] is True
    assert result["removed"] is None
    assert _food_adds(banana_details) == []

    confirmed = food_logging.modify_food(
        banana_details,
        store,
        TODAY,
        "breakfast",
        query="coffee",
        draft_id=result["draft_id"],
        option=1,
    )
    assert confirmed["removed"] == "Coffee, 1 cup"
    assert confirmed["source"] == "draft"
    assert len(_posts_to(banana_details, "food/remove")) == 1
    assert len(_posts_to(banana_details, "food/add")) == 1
