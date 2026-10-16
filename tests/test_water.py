import datetime

import pytest

from myfitnesspal_mcp import water

TODAY = datetime.date(2026, 7, 8)


@pytest.fixture
def water_client(client, make_response):
    client.session.route(
        "GET", "food/water", make_response(json_data={"item": {"milliliters": 480}})
    )
    client.session.route("POST", "food/water", make_response(status_code=200))
    return client


def test_log_water_adds_to_existing_total(water_client):
    result = water.log_water(water_client, TODAY, 3, "cup")

    assert result == {
        "day": "2026-07-08",
        "previous_ml": 480.0,
        "water_ml": 1200.0,
        "amount": 3,
        "unit": "cup",
        "replaced": False,
    }
    method, url, kwargs = water_client.session.calls[-1]
    assert method == "POST"
    assert "food/water" in url
    assert kwargs["data"] == {"milliliters": 1200.0, "date": "2026-07-08"}
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"
    assert kwargs["headers"]["Origin"] == "https://www.myfitnesspal.com"
    assert kwargs["headers"]["Referer"] == "https://www.myfitnesspal.com/food/diary"


def test_log_water_replace_sets_total(water_client):
    result = water.log_water(water_client, TODAY, 2, "cups", replace=True)

    assert result["previous_ml"] == 480.0
    assert result["water_ml"] == 480.0
    assert water_client.session.calls[-1][2]["data"]["milliliters"] == 480.0


def test_log_water_replace_with_zero_clears_day(water_client):
    result = water.log_water(water_client, TODAY, 0, "ml", replace=True)
    assert result["water_ml"] == 0.0


@pytest.mark.parametrize(
    ("unit", "canonical"),
    [
        ("ml", "ml"),
        ("Milliliters", "ml"),
        ("millilitres", "ml"),
        ("L", "l"),
        ("litres", "l"),
        ("liter", "l"),
        ("Cups", "cup"),
        ("fl_oz", "fl_oz"),
        ("fl oz", "fl_oz"),
        ("fl. oz", "fl_oz"),
        ("floz", "fl_oz"),
        ("oz", "fl_oz"),
        ("fluid ounces", "fl_oz"),
    ],
)
def test_normalize_water_unit_accepts_common_spellings(unit, canonical):
    assert water.normalize_water_unit(unit) == canonical


@pytest.mark.parametrize(
    ("amount", "unit", "expected_added_ml"),
    [(500, "ml", 500.0), (8, "fl oz", 236.588), (1.5, "litres", 1500.0)],
)
def test_log_water_converts_units(water_client, amount, unit, expected_added_ml):
    result = water.log_water(water_client, TODAY, amount, unit)
    assert result["water_ml"] - result["previous_ml"] == pytest.approx(
        expected_added_ml
    )


def test_log_water_rejects_unknown_unit_before_network_call(client):
    with pytest.raises(ValueError, match="unknown water unit 'gallon'"):
        water.log_water(client, TODAY, 1, "gallon")
    assert client.session.calls == []


def test_log_water_rejects_non_positive_add_before_network_call(client):
    with pytest.raises(ValueError, match="use replace=True"):
        water.log_water(client, TODAY, 0, "cup")
    assert client.session.calls == []


def test_log_water_rejects_negative_replace_before_network_call(client):
    with pytest.raises(ValueError, match="can't be negative"):
        water.log_water(client, TODAY, -1, "cup", replace=True)
    assert client.session.calls == []
