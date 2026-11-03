from datetime import date

from lxml import html as lh

from .diary_tables import column_name, parse_number, rows_until_classed
from .entry_matching import (
    DiaryLookupError,
    collapse_whitespace,
    normalize_meal,
    resolve_entry,
)
from .mfp_web import (
    form_headers,
    get_document,
    get_page,
    post_legacy_delete,
    require_status,
    site_url,
)

DEFAULT_MEAL_POSITIONS = {"breakfast": 0, "lunch": 1, "dinner": 2, "snacks": 3}


class UnknownMeal(DiaryLookupError):
    pass


class DiarySignedOut(RuntimeError):
    pass


def _is_meal_header(row) -> bool:
    return "meal_header" in (row.get("class") or "")


def _header_label(header_row, position: int) -> str:
    cells = header_row.xpath("./td")
    name = collapse_whitespace(cells[0].text_content()) if cells else ""
    return name or f"Meal {position + 1}"


def meal_headers(doc) -> list[str]:
    header_rows = [row for row in doc.xpath("//tr") if _is_meal_header(row)]
    return [_header_label(row, position) for position, row in enumerate(header_rows)]


def resolve_meal(doc, meal: str) -> tuple[str, str]:
    headers = meal_headers(doc)
    target = normalize_meal(meal)
    for position, header in enumerate(headers):
        if normalize_meal(header) == target:
            return str(position), header
    default_position = DEFAULT_MEAL_POSITIONS.get(target)
    if default_position is not None and default_position < len(headers):
        header_at_default = headers[default_position]
        if normalize_meal(header_at_default) not in DEFAULT_MEAL_POSITIONS:
            return str(default_position), header_at_default
    raise UnknownMeal(
        f"no MyFitnessPal meal named {meal!r}. Configured meals: " + ", ".join(headers)
    )


def food_diary_url(client, day: date | None = None) -> str:
    return site_url(client, "food/diary", day)


def _require_meals(doc: lh.HtmlElement) -> lh.HtmlElement:
    if not meal_headers(doc):
        raise DiarySignedOut(
            "the MyFitnessPal diary page has no meal sections; the login "
            "session has likely expired"
        )
    return doc


def diary_page(client, day: date) -> tuple[lh.HtmlElement, str]:
    doc, csrf = get_page(client, food_diary_url(client, day))
    return _require_meals(doc), csrf


def read_day(client, day: date) -> dict:
    doc = _require_meals(get_document(client, food_diary_url(client, day)))
    return {"foods": logged_foods(doc), "goals": daily_goals(doc)}


def _nutrient_columns(doc) -> list[str]:
    headers = [row for row in doc.xpath("//tr") if _is_meal_header(row)]
    return (
        [column_name(cell) for cell in headers[0].xpath("./td")[1:]] if headers else []
    )


def _nutrient_value(cell) -> float:
    """A cell can wrap the number in a macro-value span beside its percentage."""
    spans = cell.xpath("./span[@class='macro-value']")
    return parse_number(spans[0].text if spans else cell.text)


def _nutrition(row, columns: list[str]) -> dict[str, float]:
    cells = row.xpath("./td")[1:]
    return {
        name: _nutrient_value(cell)
        for name, cell in zip(columns, cells, strict=False)
        if name
    }


def _food_name(cell) -> str:
    anchors = cell.xpath("./a")
    return (anchors[0] if anchors else cell).text_content().strip()


def logged_foods(doc) -> list[dict]:
    columns = _nutrient_columns(doc)
    header_rows = [row for row in doc.xpath("//tr") if _is_meal_header(row)]
    return [
        {
            "meal": _header_label(header, position),
            "name": _food_name(row.xpath("./td")[0]),
            "nutrition": _nutrition(row, columns),
        }
        for position, header in enumerate(header_rows)
        for row in rows_until_classed(header.getnext())
    ]


def daily_goals(doc) -> dict[str, float]:
    """The goals row follows the first "total" row, under the meal columns."""
    totals = doc.xpath("//tr[@class='total']")
    goals = totals[0].getnext() if totals else None
    if goals is None:
        return {}
    return _nutrition(goals, _nutrient_columns(doc))


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
            current_meal = normalize_meal(_header_label(row, meal_position))
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


def delete_food(
    client,
    day: date,
    query: str,
    meal: str | None = None,
    *,
    page: tuple | None = None,
) -> dict:
    doc, token = page or diary_page(client, day)
    label = normalize_meal(resolve_meal(doc, meal)[1]) if meal else None
    entry = resolve_entry(diary_entries(doc), query, label, day)
    post_legacy_delete(
        client,
        "food/remove",
        entry["entry_id"],
        token,
        referer=food_diary_url(client),
    )
    return {"removed": entry["name"], "meal": entry["meal"]}
