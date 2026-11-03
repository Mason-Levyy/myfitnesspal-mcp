import pytest
from lxml import html as lh

from myfitnesspal_mcp import diary_tables


@pytest.mark.parametrize(
    "text, number",
    [
        ("1,240", 1240.0),
        ("\n   20  ", 20.0),
        ("-115", -115.0),
        ("12 st 3 lb", 171.0),
        ("12 st", 168.0),
        ("", 0),
        (None, 0),
        ("--", 0),
    ],
)
def test_parse_number(text, number):
    assert diary_tables.parse_number(text) == number


def test_column_name_ignores_the_unit_subtitle():
    cell = lh.fromstring('<td>Carbs<div class="subtitle">g</div></td>')
    assert diary_tables.column_name(cell) == "carbohydrates"


def test_rows_until_classed_stops_at_the_first_classed_row():
    doc = lh.fromstring(
        "<table><tr id='a'><td></td></tr><tr id='b'><td></td></tr>"
        "<tr class='bottom'><td></td></tr><tr id='c'><td></td></tr></table>"
    )
    first = doc.xpath("//tr")[0]
    assert [row.get("id") for row in diary_tables.rows_until_classed(first)] == [
        "a",
        "b",
    ]
