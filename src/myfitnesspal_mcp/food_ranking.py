from dataclasses import dataclass

NUTRIENTS = ("calories", "protein", "carbs", "fat")


@dataclass(frozen=True)
class MacroTargets:
    min_calories: float | None = None
    max_calories: float | None = None
    min_protein: float | None = None
    max_protein: float | None = None
    min_carbs: float | None = None
    max_carbs: float | None = None
    min_fat: float | None = None
    max_fat: float | None = None

    def bounds(self) -> dict[str, tuple[float | None, float | None]]:
        bounded = {}
        for nutrient in NUTRIENTS:
            lower = getattr(self, f"min_{nutrient}")
            upper = getattr(self, f"max_{nutrient}")
            if lower is not None or upper is not None:
                bounded[nutrient] = (lower, upper)
        return bounded

    def validate(self) -> None:
        for nutrient, (lower, upper) in self.bounds().items():
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"min_{nutrient} is greater than max_{nutrient}")


def normalize_query(query: str) -> str:
    return " ".join(query.lower().split())


def entry_nutrition(
    base: dict, nutrition_multiplier: float | None, quantity: float
) -> dict[str, float | None]:
    if nutrition_multiplier is None:
        return dict.fromkeys(NUTRIENTS)
    factor = nutrition_multiplier * quantity
    scaled = {}
    for nutrient in NUTRIENTS:
        value = base.get(nutrient)
        scaled[nutrient] = None if value is None else round(value * factor, 1)
    return scaled


def target_shortfall(nutrition: dict, targets: MacroTargets) -> float:
    shortfall = 0.0
    for nutrient, (lower, upper) in targets.bounds().items():
        value = nutrition.get(nutrient)
        if value is None:
            shortfall += 1.0
            continue
        if lower is not None and value < lower:
            shortfall += (lower - value) / max(lower, 1.0)
        if upper is not None and value > upper:
            shortfall += (value - upper) / max(upper, 1.0)
    return round(shortfall, 6)


def _evaluate_servings(
    candidate: dict, quantity: float, targets: MacroTargets
) -> list[dict]:
    evaluated = []
    for serving in candidate.get("servings") or []:
        nutrition = entry_nutrition(
            candidate.get("nutrition") or {},
            serving["nutrition_multiplier"],
            quantity,
        )
        shortfall = target_shortfall(nutrition, targets)
        evaluated.append(
            {
                "weight_id": serving["weight_id"],
                "label": serving["label"],
                "nutrition": nutrition,
                "fits_targets": shortfall == 0.0,
                "shortfall": shortfall,
            }
        )
    return evaluated


def _best_serving_index(servings: list[dict]) -> int | None:
    if not servings:
        return None
    return min(
        range(len(servings)), key=lambda index: (servings[index]["shortfall"], index)
    )


def rank_candidates(
    candidates: list[dict],
    query: str,
    quantity: float = 1.0,
    targets: MacroTargets | None = None,
    *,
    pinned_food_id: str | None = None,
    pinned_weight_id: str | None = None,
) -> list[dict]:
    targets = targets or MacroTargets()
    wanted_name = normalize_query(query)
    seen_food_ids = set()
    options = []
    for candidate in candidates:
        food_id = str(candidate["food_id"])
        if food_id in seen_food_ids:
            continue
        seen_food_ids.add(food_id)
        servings = _evaluate_servings(candidate, quantity, targets)
        pinned = food_id == pinned_food_id
        best_index = _best_serving_index(servings)
        if pinned and pinned_weight_id is not None:
            for index, serving in enumerate(servings):
                if serving["weight_id"] == str(pinned_weight_id):
                    best_index = index
        if best_index is None:
            shortfall = target_shortfall({}, targets)
        else:
            shortfall = servings[best_index]["shortfall"]
        options.append(
            {
                "food_id": food_id,
                "name": candidate["name"],
                "brand": candidate.get("brand"),
                "verified": bool(candidate.get("verified")),
                "pinned": pinned,
                "exact_match": normalize_query(candidate["name"]) == wanted_name,
                "fits_targets": shortfall == 0.0,
                "shortfall": shortfall,
                "search_rank": candidate.get("search_rank"),
                "default_weight_id": candidate.get("default_weight_id"),
                "serving_index": best_index,
                "servings": servings,
            }
        )
    options.sort(
        key=lambda option: (
            not option["pinned"],
            not option["fits_targets"],
            option["shortfall"],
            not option["exact_match"],
            not option["verified"],
            option["search_rank"] is None,
            option["search_rank"] or 0,
            option["name"].lower(),
            option["food_id"],
        )
    )
    for number, option in enumerate(options, start=1):
        option["option"] = number
    return options
