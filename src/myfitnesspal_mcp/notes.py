from datetime import date
from html import unescape

from .diary import diary_page, food_diary_url
from .mfp_web import form_headers, get_json, require_status, site_url


def get_note(client, day: date) -> str | None:
    """MFP stores the body double-HTML-encoded."""
    response = get_json(client, site_url(client, "food/note", day)) or {}
    body = (response.get("item") or {}).get("body")
    if not body:
        return None
    return unescape(unescape(body)) or None


def set_note(client, day: date, body: str) -> dict:
    _, csrf = diary_page(client, day)
    resp = client.session.post(
        site_url(client, "food/note"),
        data={"body": body, "date": day.isoformat()},
        headers=form_headers(client, csrf, food_diary_url(client), accept="*/*"),
    )
    require_status(resp, "/food/note", (200, 201, 204))
    return {"day": day.isoformat(), "note": body}


def push_note(client, day: date, text: str, append: bool = False) -> dict:
    if append:
        existing = get_note(client, day)
        if existing:
            text = f"{existing}\n{text}"
    return set_note(client, day, text)
