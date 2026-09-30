from datetime import date
from urllib import parse

from .mfp_web import api_headers, require_status


def set_weight(client, day: date, value: float) -> dict:
    """Value is in the account's display unit (kg or lbs): the v2 API stores
    and echoes whatever unit the account is configured with."""
    resp = client.session.post(
        parse.urljoin(client.BASE_API_URL, "v2/measurements"),
        json={"items": [{"type": "Weight", "value": value, "date": day.isoformat()}]},
        headers=api_headers(
            client, {"Accept": "application/json", "Content-Type": "application/json"}
        ),
    )
    require_status(resp, "/v2/measurements", (200, 201))
    item = resp.json()["items"][0]
    return {"day": item["date"], "weight": item["value"], "unit": item.get("unit")}
