"""The diary's "Complete This Entry" / "Make Additional Entries" button.

MyFitnessPal posts a completed day to the news feed with a five-week weight
projection, unless the day is under its calorie minimum.
"""

from datetime import date
from urllib import parse

from . import diary


def _completion_box(doc):
    boxes = doc.xpath("//div[@id='complete_day']")
    return boxes[0] if boxes else None


def day_completion(doc) -> bool | None:
    """None when the page has no completion section: not your own diary, or
    MyFitnessPal changed the page. The button's link is the reliable signal; a
    day completed under the calorie minimum shows a warning paragraph first."""
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
        parse.urljoin(client.BASE_URL_SECURE, f"food/{action}?date={day.isoformat()}"),
        headers=diary.api_headers(
            client,
            {
                "X-CSRF-Token": csrf,
                "Origin": client.BASE_URL_SECURE.rstrip("/"),
                "Referer": diary.food_diary_url(client, day),
            },
        ),
    )
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"MyFitnessPal /food/{action} returned HTTP {resp.status_code}"
        )


def set_day_complete(client, day: date, complete: bool | None = True) -> dict:
    doc, csrf = diary.diary_page(client, day)
    current = day_completion(doc)
    if current is None:
        raise RuntimeError(
            f"the diary page for {day.isoformat()} has no 'Complete This Entry' section"
        )
    if complete is None or current == complete:
        return {
            "day": day.isoformat(),
            "complete": current,
            "changed": False,
            "message": _completion_message(doc),
        }
    _post_completion(
        client, day, "day_complete" if complete else "day_incomplete", csrf
    )
    after, _ = diary.diary_page(client, day)
    state = day_completion(after)
    if state != complete:
        wanted = "complete" if complete else "reopened"
        raise RuntimeError(
            f"MyFitnessPal accepted the request but {day.isoformat()} is still not "
            f"{wanted}"
        )
    return {
        "day": day.isoformat(),
        "complete": state,
        "changed": True,
        "message": _completion_message(after),
    }
