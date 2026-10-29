"""The diary's "Complete This Entry" / "Make Additional Entries" button.

MyFitnessPal posts a completed day to the news feed with a five-week weight
projection, unless the day is under its calorie minimum.
"""

from datetime import date

from . import diary
from .mfp_web import ORIGIN, api_headers, require_status, site_url


def _completion_box(doc):
    boxes = doc.xpath("//div[@id='complete_day']")
    return boxes[0] if boxes else None


def day_completion(doc) -> bool | None:
    """None when the page has no completion section: not your own diary, or
    MyFitnessPal changed the page. The button's link is the reliable signal; a
    day completed under the calorie minimum shows a warning paragraph first,
    which is why python-myfitnesspal's first-child check reads it as
    incomplete."""
    box = _completion_box(doc)
    if box is None:
        return None
    if box.xpath(".//a[contains(@href, 'day_incomplete')]") or box.xpath(
        ".//*[contains(@class, 'day_complete_message')]"
    ):
        return True
    if box.xpath(".//a[contains(@href, 'day_complete')]") or box.xpath(
        ".//*[contains(@class, 'day_incomplete_message')]"
    ):
        return False
    return None


def _completion_message(doc) -> str:
    box = _completion_box(doc)
    return "" if box is None else " ".join(box.text_content().split())


def _post_completion(client, day: date, action: str, csrf: str) -> None:
    resp = client.session.post(
        site_url(client, f"food/{action}", day),
        headers=api_headers(
            client,
            {
                "X-CSRF-Token": csrf,
                "Origin": ORIGIN,
                "Referer": diary.food_diary_url(client, day),
            },
        ),
    )
    require_status(resp, f"/food/{action}", (200, 204))


def _state(day: date, doc, changed: bool) -> dict:
    return {
        "day": day.isoformat(),
        "complete": day_completion(doc),
        "changed": changed,
        "message": _completion_message(doc),
    }


def set_day_complete(client, day: date, complete: bool | None = None) -> dict:
    doc, csrf = diary.diary_page(client, day)
    current = day_completion(doc)
    if current is None:
        raise RuntimeError(
            f"the diary page for {day.isoformat()} has no 'Complete This Entry' section"
        )
    if complete is None or current == complete:
        return _state(day, doc, changed=False)
    _post_completion(
        client, day, "day_complete" if complete else "day_incomplete", csrf
    )
    after, _ = diary.diary_page(client, day)
    if day_completion(after) != complete:
        wanted = "complete" if complete else "reopened"
        raise RuntimeError(
            f"MyFitnessPal accepted the request but {day.isoformat()} is still not "
            f"{wanted}"
        )
    return _state(day, after, changed=True)
