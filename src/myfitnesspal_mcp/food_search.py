import logging
from urllib import parse

from .mfp_web import get_document, site_url

logger = logging.getLogger(__name__)


def _result_extras(anchor) -> dict:
    extras = {
        "external_id": anchor.get("data-external-id"),
        "brand": None,
        "serving": None,
        "calories": None,
    }
    containers = anchor.xpath("ancestor::li[1]")
    if not containers:
        return extras
    info = containers[0].xpath(".//p[@class='search-nutritional-info']")
    if not info or not info[0].text:
        return extras
    parts = info[0].text.strip().split(",")
    if len(parts) >= 3:
        extras["brand"] = " ".join(parts[0:-2]).strip()
    if len(parts) >= 2:
        extras["serving"] = parts[-2].strip() or None
    calories_text = parts[-1].replace("calories", "").strip()
    try:
        extras["calories"] = float(calories_text)
    except ValueError:
        pass
    return extras


def search_results(client, query: str) -> list[dict]:
    """MFP's legacy search page, because its food_id + weight_id are the ids
    /food/add accepts; v2 API ids are rejected."""
    doc = get_document(
        client, site_url(client, f"food/search?search={parse.quote(query)}&page=1")
    )
    results = []
    for anchor in doc.xpath("//a[@data-original-id and @data-weight-ids]"):
        weight_ids = [w for w in anchor.get("data-weight-ids").split(",") if w]
        if not weight_ids:
            continue
        result = {
            "food_id": anchor.get("data-original-id"),
            "weight_id": weight_ids[0],
            "weight_ids": weight_ids,
            "name": anchor.text_content().strip(),
        }
        result.update(_result_extras(anchor))
        results.append(result)
    return results


def serving_label(serving: dict) -> str | None:
    parts = []
    value = serving.get("value")
    if isinstance(value, (int, float)):
        parts.append(f"{value:g}")
    elif value is not None:
        parts.append(str(value))
    if serving.get("unit"):
        parts.append(str(serving["unit"]))
    return " ".join(parts) or None


def _nutrition_multiplier(serving: dict) -> float | None:
    try:
        return float(serving["nutrition_multiplier"])
    except (KeyError, TypeError, ValueError):
        return None


def pair_servings(weight_ids: list[str], serving_sizes: list[dict]) -> list[dict]:
    if len(weight_ids) != len(serving_sizes):
        return []
    servings = []
    for weight_id, serving in zip(weight_ids, serving_sizes, strict=True):
        if not isinstance(serving, dict):
            continue
        multiplier = _nutrition_multiplier(serving)
        label = serving_label(serving)
        if multiplier is None or label is None:
            continue
        servings.append(
            {"weight_id": weight_id, "label": label, "nutrition_multiplier": multiplier}
        )
    return servings


def food_details(client, external_id: str | None) -> dict | None:
    if not external_id:
        return None
    try:
        details = client._get_food_item_details(int(external_id))
        nutrition = details["nutrition"]
        return {
            "verified": details.get("verified"),
            "nutrition": {
                "calories": details["calories"],
                "protein": nutrition.get("protein"),
                "carbs": nutrition.get("carbohydrates"),
                "fat": nutrition.get("fat"),
            },
            "serving_sizes": details.get("serving_sizes") or [],
        }
    except Exception as exc:
        logger.debug("no details for food %s, using search data: %s", external_id, exc)
        return None


def food_candidates(
    client, query: str, limit: int = 10, results: list[dict] | None = None
) -> list[dict]:
    if results is None:
        results = search_results(client, query)
    candidates = []
    for search_rank, result in enumerate(results[:limit]):
        candidate = {
            "food_id": result["food_id"],
            "name": result["name"],
            "brand": result["brand"],
            "verified": None,
            "search_rank": search_rank,
            "nutrition": {"calories": result["calories"]},
            "servings": [],
            "default_weight_id": result["weight_id"],
        }
        details = food_details(client, result["external_id"])
        if details:
            candidate["verified"] = details["verified"]
            candidate["nutrition"] = details["nutrition"]
            candidate["servings"] = pair_servings(
                result["weight_ids"], details["serving_sizes"]
            )
        if not candidate["servings"]:
            candidate["nutrition"] = {"calories": result["calories"]}
            candidate["servings"] = [
                {
                    "weight_id": result["weight_id"],
                    "label": result["serving"] or "default serving",
                    "nutrition_multiplier": 1.0,
                }
            ]
        candidates.append(candidate)
    return candidates


def search_food(
    client, query: str, limit: int = 5, with_macros: bool = True
) -> list[dict]:
    candidates = []
    for result in search_results(client, query)[:limit]:
        candidate = {
            "name": result["name"],
            "brand": result["brand"],
            "calories": result["calories"],
            "protein": None,
            "carbs": None,
            "fat": None,
            "serving": None,
            "verified": None,
            "food_id": result["food_id"],
            "weight_id": result["weight_id"],
        }
        details = food_details(client, result["external_id"]) if with_macros else None
        if details:
            candidate.update(details["nutrition"])
            candidate["verified"] = details["verified"]
            if details["serving_sizes"]:
                candidate["serving"] = serving_label(details["serving_sizes"][0])
        candidates.append(candidate)
    return candidates
