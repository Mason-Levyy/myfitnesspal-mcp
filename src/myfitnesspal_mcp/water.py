from datetime import date

from .diary import diary_page, food_diary_url
from .mfp_web import form_headers, get_json, require_status, site_url

# MFP stores 1 cup as 240 mL and 1 fl oz as 29.5735 mL (verified 2026-09-28 via
# GET /food/water, matching MFP.Tools.UnitConverter.Water in the web app).
ML_PER_WATER_UNIT = {"ml": 1.0, "l": 1000.0, "cup": 240.0, "fl_oz": 29.5735}

WATER_UNIT_ALIASES = {
    "ml": "ml",
    "mls": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "millilitres": "ml",
    "l": "l",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "litres": "l",
    "cup": "cup",
    "cups": "cup",
    "floz": "fl_oz",
    "oz": "fl_oz",
    "ounce": "fl_oz",
    "ounces": "fl_oz",
    "fluidounce": "fl_oz",
    "fluidounces": "fl_oz",
}


def normalize_water_unit(unit: str) -> str:
    compact = "".join(character for character in unit.lower() if character.isalnum())
    canonical = WATER_UNIT_ALIASES.get(compact)
    if canonical is None:
        accepted = ", ".join(ML_PER_WATER_UNIT)
        raise ValueError(f"unknown water unit {unit!r}; use one of: {accepted}")
    return canonical


def get_water_ml(client, day: date) -> float:
    body = get_json(client, site_url(client, "food/water", day))
    return float(body["item"]["milliliters"])


def log_water(
    client, day: date, amount: float, unit: str = "cup", *, replace: bool = False
) -> dict:
    """/food/water takes the day's full total, so adding reads the current
    total first."""
    canonical_unit = normalize_water_unit(unit)
    if replace and amount < 0:
        raise ValueError("amount can't be negative")
    if not replace and amount <= 0:
        raise ValueError(
            "amount must be greater than zero; use replace=True to lower the total"
        )
    amount_ml = float(amount) * ML_PER_WATER_UNIT[canonical_unit]
    previous_ml = get_water_ml(client, day)
    water_ml = amount_ml if replace else previous_ml + amount_ml
    _, csrf = diary_page(client, day)
    resp = client.session.post(
        site_url(client, "food/water"),
        data={"milliliters": water_ml, "date": day.isoformat()},
        headers=form_headers(client, csrf, food_diary_url(client)),
    )
    require_status(resp, "/food/water", (200, 201, 204))
    return {
        "day": day.isoformat(),
        "previous_ml": previous_ml,
        "water_ml": water_ml,
        "amount": amount,
        "unit": canonical_unit,
        "replaced": replace,
    }
