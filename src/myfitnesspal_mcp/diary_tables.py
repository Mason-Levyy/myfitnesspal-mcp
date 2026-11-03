import re

COLUMN_ALIASES = {"carbs": "carbohydrates"}

# Stone weights render as "12 st 3 lb". From python-myfitnesspal, as is
# reading anything unparseable as 0.
STONE_AND_POUNDS = re.compile(r"(?:(?P<st>\d+) st)\W*(?:(?P<lbs>\d+) lb)?")


def parse_number(text: str | None) -> float:
    text = text or ""
    stone = STONE_AND_POUNDS.match(text)
    if stone:
        return float(stone["lbs"] or 0) + float(stone["st"]) * 14
    try:
        return float(re.sub(r"[^-\d.]+", "", text))
    except ValueError:
        return 0


def column_name(cell) -> str:
    """Reads the cell's own text: header cells also hold a unit subtitle
    ("Calories<div>kcal</div>"), which text_content() would run into it."""
    name = (cell.text or "").strip().lower()
    return COLUMN_ALIASES.get(name, name)


def rows_until_classed(row):
    """Logged rows carry no class; the first row that has one (a meal header,
    the "Add Food" row, totals) ends the section."""
    while row is not None and row.get("class") is None:
        yield row
        row = row.getnext()
