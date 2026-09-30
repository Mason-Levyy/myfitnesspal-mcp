from datetime import date

from lxml import html as lh

from .mfp_web import (
    form_headers,
    get_page,
    post_legacy_delete,
    require_status,
    site_url,
)

MEAL_ALIASES = {"snack": "snacks"}

DEFAULT_MEAL_POSITIONS = {"breakfast": 0, "lunch": 1, "dinner": 2, "snacks": 3}


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def _normalize_meal(meal: str | None) -> str | None:
    if meal is None:
        return None
    lowered = _collapse_whitespace(meal).lower()
    return MEAL_ALIASES.get(lowered, lowered)


class DiaryLookupError(RuntimeError):
    pass


class UnknownMeal(DiaryLookupError):
    pass


class DiarySignedOut(RuntimeError):
    pass


def _is_meal_header(row) -> bool:
    return "meal_header" in (row.get("class") or "")


def _header_label(header_row, position: int) -> str:
    cells = header_row.xpath("./td")
    name = _collapse_whitespace(cells[0].text_content()) if cells else ""
    return name or f"Meal {position + 1}"


def meal_headers(doc) -> list[str]:
    header_rows = [row for row in doc.xpath("//tr") if _is_meal_header(row)]
    return [_header_label(row, position) for position, row in enumerate(header_rows)]


def resolve_meal(doc, meal: str) -> tuple[str, str]:
    headers = meal_headers(doc)
    target = _normalize_meal(meal)
    for position, header in enumerate(headers):
        if _normalize_meal(header) == target:
            return str(position), header
    default_position = DEFAULT_MEAL_POSITIONS.get(target)
    if default_position is not None and default_position < len(headers):
        header_at_default = headers[default_position]
        if _normalize_meal(header_at_default) not in DEFAULT_MEAL_POSITIONS:
            return str(default_position), header_at_default
    raise UnknownMeal(
        f"no MyFitnessPal meal named {meal!r}. Configured meals: " + ", ".join(headers)
    )


def food_diary_url(client, day: date | None = None) -> str:
    return site_url(client, "food/diary", day)


def diary_page(client, day: date) -> tuple[lh.HtmlElement, str]:
    doc, csrf = get_page(client, food_diary_url(client, day))
    if not meal_headers(doc):
        raise DiarySignedOut(
            "the MyFitnessPal diary page has no meal sections; the login "
            "session has likely expired"
        )
    return doc, csrf


def push_food(
    client,
    day: date,
    meal: str,
    *,
    food_id: str,
    weight_id: str,
    quantity: float = 1.0,
    page: tuple | None = None,
) -> None:
    doc, csrf = page or diary_page(client, day)
    meal_id, _ = resolve_meal(doc, meal)
    resp = client.session.post(
        site_url(client, "food/add"),
        data={
            "food_entry[food_id]": str(food_id),
            "food_entry[date]": day.isoformat(),
            "food_entry[quantity]": str(quantity),
            "food_entry[weight_id]": str(weight_id),
            "food_entry[meal_id]": meal_id,
            "ajax": "true",
        },
        headers=form_headers(
            client, csrf, site_url(client, "food/search"), accept="application/json"
        ),
    )
    require_status(resp, "/food/add", (200, 204))


def diary_entries(doc) -> list[dict]:
    entries = []
    current_meal = None
    meal_position = -1
    for row in doc.xpath("//tr"):
        if _is_meal_header(row):
            meal_position += 1
            current_meal = _normalize_meal(_header_label(row, meal_position))
            continue
        anchors = row.xpath(".//a[@data-food-entry-id]")
        if anchors:
            entries.append(
                {
                    "entry_id": anchors[0].get("data-food-entry-id"),
                    "meal": current_meal,
                    "name": anchors[0].text_content().strip(),
                }
            )
    return entries


def _meal_pool(entries: list[dict], meal: str | None) -> list[dict]:
    target = _normalize_meal(meal)
    return entries if target is None else [e for e in entries if e["meal"] == target]


def find_entries(
    entries: list[dict], query: str, meal: str | None = None
) -> list[dict]:
    needle = query.lower()
    return [e for e in _meal_pool(entries, meal) if needle in e["name"].lower()]


class NoMatchingEntry(DiaryLookupError):
    pass


class AmbiguousEntry(DiaryLookupError):
    def __init__(
        self,
        query: str,
        meal: str | None,
        day: date,
        candidates: list[dict],
        *,
        advice: str = "Use a more specific query to pick one.",
    ):
        self.candidates = candidates
        where = f" in {meal}" if meal else ""
        options = "; ".join(f"{c['name']!r}" for c in candidates)
        super().__init__(
            f"'{query}'{where} on {day.isoformat()} matches multiple diary entries: "
            f"{options}. {advice}"
        )


def resolve_entry(entries: list[dict], query: str, meal: str | None, day: date) -> dict:
    candidates = find_entries(entries, query, meal)
    exact = [c for c in candidates if c["name"].lower() == query.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise AmbiguousEntry(query, meal, day, candidates)
    raise no_matching_entry(entries, query, meal, day)


def no_matching_entry(
    entries: list[dict], query: str, meal: str | None, day: date
) -> NoMatchingEntry:
    where = f" in {meal}" if meal else ""
    pool = _meal_pool(entries, meal)
    logged = "; ".join(f"{e['name']!r}" for e in pool) if pool else "(nothing logged)"
    return NoMatchingEntry(
        f"no diary entry matching '{query}'{where} on {day.isoformat()}. "
        f"Entries actually logged{where}: {logged}"
    )


def delete_food(
    client,
    day: date,
    query: str,
    meal: str | None = None,
    *,
    page: tuple | None = None,
) -> dict:
    doc, token = page or diary_page(client, day)
    label = _normalize_meal(resolve_meal(doc, meal)[1]) if meal else None
    entry = resolve_entry(diary_entries(doc), query, label, day)
    post_legacy_delete(
        client,
        "food/remove",
        entry["entry_id"],
        token,
        referer=food_diary_url(client),
    )
    return {"removed": entry["name"], "meal": entry["meal"]}
