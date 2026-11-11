import json
from datetime import date

from .diary_tables import parse_number
from .mfp_web import (
    api_url,
    get_document,
    json_headers,
    require_status,
    site_url,
)


def set_weight(client, day: date, value: float) -> dict:
    """Value is in the account's display unit (kg or lbs): the v2 API stores
    and echoes whatever unit the account is configured with."""
    resp = client.session.post(
        api_url(client, "measurements"),
        json={"items": [{"type": "Weight", "value": value, "date": day.isoformat()}]},
        headers=json_headers(client),
    )
    require_status(resp, "/v2/measurements", (200, 201))
    item = resp.json()["items"][0]
    return {"day": item["date"], "weight": item["value"], "unit": item.get("unit")}


def page_weights(doc) -> dict[date, float]:
    """The measurements page is a Next.js app: its entries sit in the
    dehydrated query cache in __NEXT_DATA__."""
    weights = {}
    for script in doc.xpath("//script[@id='__NEXT_DATA__']"):
        queries = json.loads(script.text)["props"]["pageProps"]["dehydratedState"]
        for query in queries["queries"]:
            data = query["state"].get("data")
            if "measurements" not in query["queryKey"] or not isinstance(data, dict):
                continue
            for item in data.get("items", []):
                if item.get("value") is not None:
                    day = date.fromisoformat(item["date"])
                    weights[day] = parse_number(str(item["value"]))
    return weights


def weight_history(client, since: date, until: date) -> dict[date, float]:
    weights: dict[date, float] = {}
    page = 1
    while True:
        url = site_url(
            client, "measurements/edit", params={"page": page, "type": "Weight"}
        )
        found = page_weights(get_document(client, url))
        weights.update(found)
        if not found or min(found) <= since:
            break
        page += 1
    return {day: value for day, value in weights.items() if since <= day <= until}
