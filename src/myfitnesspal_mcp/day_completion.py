from datetime import date

from . import diary
from .mfp_web import form_headers, require_status, site_url


def _completion_box(doc):
    boxes = doc.xpath("//div[@id='complete_day']")
    return boxes[0] if boxes else None


def is_complete(box) -> bool | None:
    if box is None:
        return None
    if box.xpath(".//a[contains(@href, 'day_incomplete')]"):
        return True
    if box.xpath(".//a[contains(@href, 'day_complete')]"):
        return False
    return None


def _message(box) -> str | None:
    if is_complete(box) is not True:
        return None
    button = " ".join(box.xpath(".//a/text()"))
    text = " ".join(box.text_content().replace(button, " ").split())
    return text or None


def _state(day: date, box, *, changed: bool) -> dict:
    return {
        "day": day.isoformat(),
        "complete": is_complete(box),
        "changed": changed,
        "message": _message(box),
    }


def day_state(client, day: date) -> dict:
    doc, _ = diary.diary_page(client, day)
    return _state(day, _completion_box(doc), changed=False)


def _describe(state: bool | None) -> str:
    if state is None:
        return "shows no 'Complete This Entry' section"
    return "is still complete" if state else "is still incomplete"


def set_day_complete(client, day: date, complete: bool) -> dict:
    doc, csrf = diary.diary_page(client, day)
    box = _completion_box(doc)
    current = is_complete(box)
    if current is None:
        raise RuntimeError(
            f"the diary page for {day.isoformat()} has no 'Complete This Entry' "
            "section, so the day can't be changed from here. MyFitnessPal may have "
            "changed the page; the button on the website's Food tab still works."
        )
    if current == complete:
        return _state(day, box, changed=False)
    action = "day_complete" if complete else "day_incomplete"
    resp = client.session.post(
        site_url(client, f"food/{action}", day),
        headers=form_headers(client, csrf, diary.food_diary_url(client, day)),
    )
    require_status(resp, f"/food/{action}", (200, 204))
    after, _ = diary.diary_page(client, day)
    after_box = _completion_box(after)
    state = is_complete(after_box)
    if state != complete:
        raise RuntimeError(
            f"MyFitnessPal accepted the request, but {day.isoformat()} "
            f"{_describe(state)}"
        )
    return _state(day, after_box, changed=True)
