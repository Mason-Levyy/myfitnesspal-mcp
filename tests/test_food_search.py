from myfitnesspal_mcp import food_search


def test_search_results_parse_ids_and_listing(client):
    results = food_search.search_results(client, "banana")
    assert [r["food_id"] for r in results] == ["111", "222"]
    first = results[0]
    assert first["weight_id"] == "10"
    assert first["name"] == "Banana"
    assert first["external_id"] == "999"
    assert first["brand"] == "Fresh Fruit"
    assert first["calories"] == 105.0


def test_search_results_degrade_without_external_id(client):
    results = food_search.search_results(client, "banana")
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
    candidates = food_search.search_food(client, "banana", limit=2)
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
    candidates = food_search.search_food(client, "banana", limit=1)
    assert candidates[0]["calories"] == 105.0
    assert candidates[0]["protein"] is None


def test_pair_servings_zips_weight_ids_by_position():
    serving_sizes = [
        {"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0},
        {"value": 1.0, "unit": "large", "nutrition_multiplier": 1.15},
    ]
    assert food_search.pair_servings(["10", "20"], serving_sizes) == [
        {"weight_id": "10", "label": "1 medium", "nutrition_multiplier": 1.0},
        {"weight_id": "20", "label": "1 large", "nutrition_multiplier": 1.15},
    ]


def test_pair_servings_refuses_count_mismatch():
    serving_sizes = [{"value": 1.0, "unit": "medium", "nutrition_multiplier": 1.0}]
    assert food_search.pair_servings(["10", "20"], serving_sizes) == []


def test_pair_servings_skips_malformed_entries_without_shifting_weight_ids():
    serving_sizes = [
        {"value": None, "unit": None, "nutrition_multiplier": 1.0},
        {"value": 1.0, "unit": "large"},
        "not a serving",
        {"value": 2, "unit": None, "nutrition_multiplier": "2.0"},
        {"value": 40, "unit": "g", "nutrition_multiplier": 0.4},
    ]
    assert food_search.pair_servings(["10", "20", "30", "40", "50"], serving_sizes) == [
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
    candidates = food_search.food_candidates(client, "banana")
    assert [candidate["name"] for candidate in candidates] == [
        "Banana",
        "Banana Bread",
    ]
    assert candidates[0]["servings"] == [
        {"weight_id": "10", "label": "1 medium", "nutrition_multiplier": 1.0}
    ]
    assert candidates[0]["nutrition"] == {"calories": 105.0}


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
    banana, banana_bread = food_search.food_candidates(client, "banana")
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
    assert banana_bread["servings"] == [
        {"weight_id": "30", "label": "1 slice", "nutrition_multiplier": 1.0}
    ]
    assert banana_bread["default_weight_id"] == "30"
    assert banana_bread["nutrition"] == {"calories": 196.0}
