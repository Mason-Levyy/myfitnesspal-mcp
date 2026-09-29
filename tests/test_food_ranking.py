import pytest

from myfitnesspal_mcp.food_ranking import (
    MacroTargets,
    entry_nutrition,
    normalize_query,
    rank_candidates,
    target_shortfall,
)


def _candidate(food_id, name, calories, protein, verified=False, servings=None):
    return {
        "food_id": food_id,
        "name": name,
        "brand": None,
        "verified": verified,
        "nutrition": {"calories": calories, "protein": protein, "carbs": 0, "fat": 0},
        "servings": servings
        or [
            {
                "weight_id": f"w{food_id}",
                "label": "1 serving",
                "nutrition_multiplier": 1.0,
            }
        ],
    }


def test_normalize_query_collapses_case_and_whitespace():
    assert normalize_query("  Greek   YOGURT ") == "greek yogurt"


def test_entry_nutrition_scales_by_multiplier_and_quantity():
    base = {"calories": 100, "protein": 10, "carbs": None, "fat": 2}
    assert entry_nutrition(base, 1.5, 2) == {
        "calories": 300.0,
        "protein": 30.0,
        "carbs": None,
        "fat": 6.0,
    }


def test_target_shortfall_is_zero_inside_bounds():
    targets = MacroTargets(min_protein=30, max_calories=500)
    assert target_shortfall({"calories": 400, "protein": 35}, targets) == 0.0


def test_target_shortfall_measures_relative_miss_and_unknowns():
    targets = MacroTargets(min_protein=40, max_calories=500)
    assert target_shortfall({"calories": 600, "protein": 30}, targets) == pytest.approx(
        100 / 500 + 10 / 40
    )
    assert target_shortfall({"calories": 400, "protein": None}, targets) == 1.0


def test_targets_reject_inverted_range():
    with pytest.raises(ValueError, match="min_protein is greater than max_protein"):
        MacroTargets(min_protein=50, max_protein=10).validate()


def test_rank_is_stable_regardless_of_input_order():
    candidates = [
        _candidate("3", "Banana Bread", 196, 3),
        _candidate("1", "Banana", 105, 1, verified=True),
        _candidate("2", "banana chips", 150, 1),
    ]
    forward = rank_candidates(candidates, "banana")
    backward = rank_candidates(list(reversed(candidates)), "banana")
    assert [o["food_id"] for o in forward] == [o["food_id"] for o in backward]
    assert [o["food_id"] for o in forward] == ["1", "3", "2"]
    assert [o["option"] for o in forward] == [1, 2, 3]


def test_rank_prefers_pinned_food_first():
    candidates = [
        _candidate("1", "Banana", 105, 1, verified=True),
        _candidate("2", "Banana, organic", 110, 1),
    ]
    options = rank_candidates(candidates, "banana", pinned_food_id="2")
    assert options[0]["food_id"] == "2"
    assert options[0]["pinned"] is True


def test_rank_puts_target_fits_first_and_flags_near_misses():
    candidates = [
        _candidate("1", "Chicken Breast", 165, 31),
        _candidate("2", "Chicken Thigh", 209, 26),
        _candidate("3", "Fried Chicken", 450, 20),
    ]
    targets = MacroTargets(min_protein=25, max_calories=250)
    options = rank_candidates(candidates, "chicken", targets=targets)
    assert [o["food_id"] for o in options] == ["1", "2", "3"]
    assert [o["fits_targets"] for o in options] == [True, True, False]


def test_targets_apply_to_whole_entry_via_quantity():
    candidates = [_candidate("1", "Egg", 70, 6)]
    targets = MacroTargets(min_protein=18)
    assert rank_candidates(candidates, "egg", 2, targets)[0]["fits_targets"] is False
    assert rank_candidates(candidates, "egg", 3, targets)[0]["fits_targets"] is True


def test_rank_selects_best_fitting_serving():
    servings = [
        {"weight_id": "small", "label": "1 small", "nutrition_multiplier": 0.5},
        {"weight_id": "large", "label": "1 large", "nutrition_multiplier": 2.0},
    ]
    candidates = [_candidate("1", "Salmon", 200, 20, servings=servings)]
    option = rank_candidates(
        candidates, "salmon", targets=MacroTargets(min_protein=35)
    )[0]
    assert option["serving_index"] == 1
    assert option["servings"][1]["nutrition"]["protein"] == 40.0
    assert option["fits_targets"] is True


def test_rank_dedupes_food_ids():
    candidates = [_candidate("1", "Banana", 105, 1), _candidate("1", "Banana", 105, 1)]
    assert len(rank_candidates(candidates, "banana")) == 1


def test_candidate_without_servings_has_no_serving_index():
    candidate = _candidate("1", "Mystery", 100, 1)
    candidate["servings"] = []
    option = rank_candidates([candidate], "mystery")[0]
    assert option["serving_index"] is None
    assert option["fits_targets"] is True


def test_search_position_breaks_ties_before_name():
    zucchini = _candidate("1", "Zucchini bread", 200, 3)
    zucchini["search_rank"] = 0
    apple = _candidate("2", "Apple bread", 200, 3)
    apple["search_rank"] = 1
    options = rank_candidates([apple, zucchini], "bread")
    assert [o["food_id"] for o in options] == ["1", "2"]


def test_pinned_food_uses_pinned_serving():
    servings = [
        {"weight_id": "small", "label": "1 small", "nutrition_multiplier": 0.5},
        {"weight_id": "large", "label": "1 large", "nutrition_multiplier": 2.0},
    ]
    candidates = [_candidate("1", "Salmon", 200, 20, servings=servings)]
    option = rank_candidates(
        candidates, "salmon", pinned_food_id="1", pinned_weight_id="large"
    )[0]
    assert option["serving_index"] == 1
