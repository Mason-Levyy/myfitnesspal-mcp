"""Diary reads and writes against MyFitnessPal's web endpoints.

MFP has no official write API. These calls replicate what the web app's own
JavaScript sends: the legacy /food/search page exposes per-result food_id +
weight_id (the ids /food/add accepts — v2 API ids are rejected), and writes
authenticate with the session's Bearer token plus mfp-* client headers and the
page csrf token.
"""

from datetime import date
from html import unescape
from urllib import parse

from lxml import html as lh

MEAL_INDEX = {"breakfast": "0", "lunch": "1", "dinner": "2", "snacks": "3", "snack": "3"}

MEALS = ("breakfast", "lunch", "dinner", "snacks")


def api_headers(client, extra: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {client.access_token}",
        "mfp-client-id": "mfp-main-js",
        "mfp-user-id": str(client.user_id),
        "X-Requested-With": "XMLHttpRequest",
    }
    if extra:
        headers.update(extra)
    return headers


def _result_extras(anchor) -> dict:
    extras = {"external_id": anchor.get("data-external-id"), "brand": None, "calories": None}
    containers = anchor.xpath("ancestor::li[1]")
    if not containers:
        return extras
    info = containers[0].xpath(".//p[@class='search-nutritional-info']")
    if not info or not info[0].text:
        return extras
    parts = info[0].text.strip().split(",")
    if len(parts) >= 3:
        extras["brand"] = " ".join(parts[0:-2]).strip()
    calories_text = parts[-1].replace("calories", "").strip()
    try:
        extras["calories"] = float(calories_text)
    except ValueError:
        pass
    return extras


def food_search(client, query: str):
    """Returns (results, csrf_token) scraped from the legacy web search page.

    Each result: {food_id, weight_id, name, external_id, brand, calories} —
    food_id/weight_id feed /food/add; external_id feeds the v2 details API.
    """
    url = parse.urljoin(
        client.BASE_URL_SECURE, f"food/search?search={parse.quote(query)}&page=1"
    )
    resp = client.session.get(url, headers=api_headers(client))
    resp.raise_for_status()
    doc = lh.fromstring(resp.text)
    csrf = doc.xpath("//meta[@name='csrf-token']/@content")
    results = []
    for anchor in doc.xpath("//a[@data-original-id and @data-weight-ids]"):
        weight_ids = [w for w in anchor.get("data-weight-ids").split(",") if w]
        if not weight_ids:
            continue
        result = {
            "food_id": anchor.get("data-original-id"),
            "weight_id": weight_ids[0],
            "name": anchor.text_content().strip(),
        }
        result.update(_result_extras(anchor))
        results.append(result)
    if csrf:
        return results, csrf[0]
    return results, None


def _serving_label(serving_sizes: list) -> str | None:
    if not serving_sizes:
        return None
    first = serving_sizes[0]
    return f"{first.get('value')} {first.get('unit')}".strip()


def search_food(client, query: str, limit: int = 5, with_macros: bool = True) -> list[dict]:
    results, _ = food_search(client, query)
    candidates = []
    for result in results[:limit]:
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
        if with_macros and result["external_id"]:
            try:
                details = client._get_food_item_details(int(result["external_id"]))
                nutrition = details["nutrition"]
                candidate["calories"] = details["calories"]
                candidate["protein"] = nutrition.get("protein")
                candidate["carbs"] = nutrition.get("carbohydrates")
                candidate["fat"] = nutrition.get("fat")
                candidate["serving"] = _serving_label(details["serving_sizes"])
                candidate["verified"] = details["verified"]
            except Exception:
                pass
        candidates.append(candidate)
    return candidates


def add_food_to_diary(client, food_id, weight_id, csrf, meal: str, day: date, quantity):
    resp = client.session.post(
        parse.urljoin(client.BASE_URL_SECURE, "food/add"),
        data={
            "food_entry[food_id]": str(food_id),
            "food_entry[date]": day.isoformat(),
            "food_entry[quantity]": str(quantity),
            "food_entry[weight_id]": str(weight_id),
            "food_entry[meal_id]": MEAL_INDEX.get(meal.lower(), "0"),
            "ajax": "true",
        },
        headers=api_headers(
            client,
            {
                "Accept": "application/json",
                "X-CSRF-Token": csrf,
                "Origin": "https://www.myfitnesspal.com",
                "Referer": parse.urljoin(client.BASE_URL_SECURE, "food/search"),
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        ),
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"MyFitnessPal /food/add returned HTTP {resp.status_code}")


def push_food(
    client,
    day: date,
    meal: str,
    query: str,
    quantity: float = 1.0,
    food_id: str | None = None,
    weight_id: str | None = None,
) -> dict:
    if food_id is not None and weight_id is not None:
        _, csrf = diary_page(client, day)
        matched = query
    else:
        results, csrf = food_search(client, query)
        if not results:
            raise RuntimeError(f"no MyFitnessPal food found for '{query}'")
        top = results[0]
        food_id = top["food_id"]
        weight_id = top["weight_id"]
        matched = top["name"]
    if not csrf:
        raise RuntimeError(
            "couldn't read the MyFitnessPal csrf token (try re-authenticating)"
        )
    add_food_to_diary(client, food_id, weight_id, csrf, meal, day, quantity)
    return {"matched": matched, "food_id": food_id}


def diary_page(client, day: date):
    url = parse.urljoin(
        client.BASE_URL_SECURE,
        f"food/diary/{client.effective_username}?date={day.isoformat()}",
    )
    resp = client.session.get(url, headers=api_headers(client))
    resp.raise_for_status()
    doc = lh.fromstring(resp.text)
    tokens = doc.xpath("//meta[@name='csrf-token']/@content")
    if not tokens:
        raise RuntimeError("couldn't read the MyFitnessPal csrf token")
    return doc, tokens[0]


def diary_entries(doc) -> list[dict]:
    """Walks the diary table: meal_header rows delimit meals; the
    data-food-entry-id anchors that follow belong to that meal."""
    entries = []
    current_meal = None
    for tr in doc.xpath("//tr"):
        classes = tr.get("class") or ""
        if "meal_header" in classes:
            header = " ".join(t.strip() for t in tr.xpath(".//text()") if t.strip())
            if header:
                current_meal = header.split()[0].lower()
            else:
                current_meal = None
            continue
        anchors = tr.xpath(".//a[@data-food-entry-id]")
        if anchors:
            entries.append(
                {
                    "entry_id": anchors[0].get("data-food-entry-id"),
                    "meal": current_meal,
                    "name": anchors[0].text_content().strip(),
                }
            )
    return entries


def find_entry(entries: list[dict], query: str, meal: str | None) -> dict | None:
    needle = query.lower()
    if meal is None:
        pool = entries
    else:
        pool = [e for e in entries if e["meal"] == meal.lower()]
    for entry in pool:
        if needle in entry["name"].lower():
            return entry
    return None


def remove_entry(client, entry_id: str, token: str) -> None:
    resp = client.session.post(
        parse.urljoin(client.BASE_URL_SECURE, f"food/remove/{entry_id}"),
        data={"_method": "delete", "authenticity_token": token},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://www.myfitnesspal.com",
            "Referer": parse.urljoin(
                client.BASE_URL_SECURE, f"food/diary/{client.effective_username}"
            ),
        },
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(f"MyFitnessPal /food/remove returned HTTP {resp.status_code}")


class NoMatchingEntry(RuntimeError):
    pass


def delete_food(client, day: date, query: str, meal: str | None = None) -> dict:
    doc, token = diary_page(client, day)
    entry = find_entry(diary_entries(doc), query, meal)
    if entry is None:
        where = ""
        if meal:
            where = f" in {meal}"
        raise NoMatchingEntry(
            f"no diary entry matching '{query}'{where} on {day.isoformat()}"
        )
    remove_entry(client, entry["entry_id"], token)
    return {"removed": entry["name"], "meal": entry["meal"]}


def modify_food(
    client,
    day: date,
    meal: str,
    query: str,
    new_query: str | None = None,
    quantity: float = 1.0,
) -> dict:
    """Delete + add are sequential; if the add fails the delete has already
    applied."""
    removed = delete_food(client, day, query, meal)
    added = push_food(client, day, meal, new_query or query, quantity)
    return {"removed": removed["removed"], "added": added["matched"], "meal": meal}


def set_weight(client, day: date, value: float) -> dict:
    """Value is in the account's display unit (kg or lbs) — the v2 API stores
    and echoes whatever unit the account is configured with."""
    resp = client.session.post(
        parse.urljoin(client.BASE_API_URL, "v2/measurements"),
        json={"items": [{"type": "Weight", "value": value, "date": day.isoformat()}]},
        headers=api_headers(
            client, {"Accept": "application/json", "Content-Type": "application/json"}
        ),
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"MyFitnessPal /v2/measurements returned HTTP {resp.status_code}"
        )
    item = resp.json()["items"][0]
    return {"day": item["date"], "weight": item["value"], "unit": item.get("unit")}


def get_note(client, day: date) -> str | None:
    """Reads the day's free-text diary note (the 'Notes' box at the bottom of
    the food diary). MFP stores the body double-HTML-encoded; returns the
    decoded text, or None when the day has no note."""
    url = (
        parse.urljoin(client.BASE_URL_SECURE, "food/note")
        + f"?date={day.isoformat()}"
    )
    resp = client.session.get(
        url, headers=api_headers(client, {"Accept": "application/json"})
    )
    resp.raise_for_status()
    item = (resp.json() or {}).get("item") or {}
    body = item.get("body")
    if not body:
        return None
    return unescape(unescape(body)) or None


def set_note(client, day: date, body: str) -> dict:
    """Writes the day's diary note, replacing whatever was there. Mirrors the
    web app's Save Note request: a form POST with the page csrf token."""
    _, csrf = diary_page(client, day)
    resp = client.session.post(
        parse.urljoin(client.BASE_URL_SECURE, "food/note"),
        data={"body": body, "date": day.isoformat()},
        headers=api_headers(
            client,
            {
                "Accept": "*/*",
                "X-CSRF-Token": csrf,
                "Origin": "https://www.myfitnesspal.com",
                "Referer": parse.urljoin(
                    client.BASE_URL_SECURE, f"food/diary/{client.effective_username}"
                ),
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        ),
    )
    if resp.status_code not in (200, 201, 204):
        raise RuntimeError(f"MyFitnessPal /food/note returned HTTP {resp.status_code}")
    return {"day": day.isoformat(), "note": body}


def push_note(client, day: date, text: str, append: bool = False) -> dict:
    """Writes `text` as the day's diary note. With append=True, keeps the
    existing note and adds `text` on a new line."""
    if append:
        existing = get_note(client, day)
        if existing:
            text = f"{existing}\n{text}"
    return set_note(client, day, text)


def get_exercise(client, day: date) -> dict:
    sections = {}
    for section in client._get_exercises(day):
        sections[section.name.lower()] = section.get_as_list()
    return {"day": day.isoformat(), "exercise": sections}
