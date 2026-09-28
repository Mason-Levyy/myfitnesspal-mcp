"""Draft-then-confirm food logging.

A draft freezes ranked search options (with every serving size) in the local
store; logging picks an option and serving from it by number, so what gets
logged never depends on MyFitnessPal's search order at confirm time.
Confirmed picks are pinned: the same words then log the same food and
serving without searching.
"""

from dataclasses import asdict
from datetime import date

from . import diary
from .food_ranking import MacroTargets, rank_candidates


class DraftNotFound(RuntimeError):
    pass


def _pinned_serving(pin: dict) -> dict:
    return {
        "weight_id": pin["weight_id"],
        "label": pin["serving"] or "pinned serving",
        "nutrition_multiplier": None,
    }


def _pinned_candidate(pin: dict) -> dict:
    return {
        "food_id": pin["food_id"],
        "name": pin["name"] or pin["query"],
        "brand": None,
        "verified": None,
        "search_rank": None,
        "nutrition": {},
        "servings": [_pinned_serving(pin)],
        "default_weight_id": pin["weight_id"],
    }


def _include_pin(candidates: list[dict], pin: dict) -> None:
    """Makes sure the pinned food and its exact serving are on offer, even
    when search dropped the food or its details didn't list that serving —
    otherwise confirming would silently re-pin a different serving."""
    for candidate in candidates:
        if str(candidate["food_id"]) != pin["food_id"]:
            continue
        serving_weight_ids = {serving["weight_id"] for serving in candidate["servings"]}
        if pin["weight_id"] not in serving_weight_ids:
            candidate["servings"].append(_pinned_serving(pin))
        return
    candidates.append(_pinned_candidate(pin))


def _option_view(option: dict) -> dict:
    return {
        "option": option["option"],
        "name": option["name"],
        "brand": option["brand"],
        "verified": option["verified"],
        "pinned": option["pinned"],
        "fits_targets": option["fits_targets"],
        "suggested_serving": option["serving_index"],
        "servings": [
            {
                "serving": index,
                "label": serving["label"],
                **serving["nutrition"],
                "fits_targets": serving["fits_targets"],
            }
            for index, serving in enumerate(option["servings"])
        ],
    }


def _draft_view(draft_id: str, body: dict) -> dict:
    return {
        "draft_id": draft_id,
        "query": body["query"],
        "day": body["day"],
        "meal": body["meal"],
        "quantity": body["quantity"],
        "targets": body["targets"],
        "options": [_option_view(option) for option in body["options"]],
    }


def _build_draft(
    client,
    store,
    query: str,
    day: date,
    meal: str,
    quantity: float,
    targets: MacroTargets,
    limit: int,
) -> tuple[str, dict]:
    targets.validate()
    pin = store.pin(query)
    candidates = diary.food_candidates(client, query, limit)
    if pin:
        _include_pin(candidates, pin)
    if not candidates:
        raise RuntimeError(f"no MyFitnessPal food found for '{query}'")
    options = rank_candidates(
        candidates,
        query,
        quantity,
        targets,
        pinned_food_id=pin["food_id"] if pin else None,
        pinned_weight_id=pin["weight_id"] if pin else None,
    )
    body = {
        "query": query,
        "day": day.isoformat(),
        "meal": meal,
        "quantity": quantity,
        "targets": {k: v for k, v in asdict(targets).items() if v is not None},
        "options": options,
    }
    return store.save_draft(body), body


def draft_food(
    client,
    store,
    query: str,
    day: date,
    meal: str = "breakfast",
    quantity: float = 1.0,
    targets: MacroTargets | None = None,
    limit: int = 10,
) -> dict:
    draft_id, body = _build_draft(
        client, store, query, day, meal, quantity, targets or MacroTargets(), limit
    )
    return _draft_view(draft_id, body)


def _chosen_serving(option: dict, serving: int | None) -> tuple[str, str | None]:
    servings = option["servings"]
    if not servings:
        if serving not in (None, 0):
            raise ValueError(
                f"option {option['option']} has no known serving sizes; omit serving"
            )
        return option["default_weight_id"], None
    index = option["serving_index"] if serving is None else serving
    if not 0 <= index < len(servings):
        raise ValueError(
            f"serving must be between 0 and {len(servings) - 1} for option "
            f"{option['option']}"
        )
    return servings[index]["weight_id"], servings[index]["label"]


def _log_option(
    client, option: dict, serving: int | None, day: date, meal: str, quantity: float
) -> dict:
    weight_id, label = _chosen_serving(option, serving)
    diary.push_food(
        client,
        day,
        meal,
        option["name"],
        quantity,
        food_id=option["food_id"],
        weight_id=weight_id,
    )
    return {
        "logged": option["name"],
        "food_id": option["food_id"],
        "weight_id": weight_id,
        "serving": label,
        "quantity": quantity,
        "meal": meal,
        "date": day.isoformat(),
    }


def log_from_draft(
    client,
    store,
    draft_id: str,
    option: int,
    serving: int | None = None,
    quantity: float | None = None,
    meal: str | None = None,
    day: date | None = None,
    pin: bool = True,
) -> dict:
    body = store.draft(draft_id)
    if body is None:
        raise DraftNotFound(
            f"draft {draft_id!r} not found or expired (drafts last 24 hours); "
            "call fitness_draft_food again"
        )
    options = body["options"]
    if not 1 <= option <= len(options):
        raise ValueError(f"option must be between 1 and {len(options)}")
    chosen = options[option - 1]
    result = _log_option(
        client,
        chosen,
        serving,
        day or date.fromisoformat(body["day"]),
        meal or body["meal"],
        body["quantity"] if quantity is None else quantity,
    )
    if pin:
        store.set_pin(
            body["query"],
            chosen["food_id"],
            result["weight_id"],
            chosen["name"],
            result["serving"],
        )
    return {**result, "source": "draft", "pinned": pin}


def log_by_query(
    client, store, query: str, day: date, meal: str, quantity: float
) -> dict:
    """Logs without asking only when the choice is already unambiguous: a
    pinned food (no search at all) or exactly one exact-name match. Anything
    else returns a draft to choose from and logs nothing."""
    pin = store.pin(query)
    if pin:
        diary.push_food(
            client,
            day,
            meal,
            pin["name"] or query,
            quantity,
            food_id=pin["food_id"],
            weight_id=pin["weight_id"],
        )
        return {
            "logged": pin["name"] or query,
            "food_id": pin["food_id"],
            "weight_id": pin["weight_id"],
            "serving": pin["serving"],
            "quantity": quantity,
            "meal": meal,
            "date": day.isoformat(),
            "source": "pin",
        }
    draft_id, body = _build_draft(
        client, store, query, day, meal, quantity, MacroTargets(), limit=10
    )
    exact = [option for option in body["options"] if option["exact_match"]]
    if len(exact) == 1:
        result = _log_option(client, exact[0], None, day, meal, quantity)
        return {**result, "source": "exact_match"}
    return {"logged": None, "needs_choice": True, **_draft_view(draft_id, body)}
