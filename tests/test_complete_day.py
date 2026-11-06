import asyncio
import datetime

import pytest
from lxml import html as lh

from myfitnesspal_mcp import day_completion, mfp_client, server

TODAY = datetime.date(2026, 7, 8)

INCOMPLETE = (
    '<div id="complete_day"><span class="day_incomplete_message"> When you\'re '
    'finished logging, click here: <a class="button complete-this-day-button" '
    'href="/food/day_complete?date=2026-07-08">Complete This Entry</a></span></div>'
)
COMPLETE = (
    '<div id="complete_day"><span class="day_complete_message">Your Food Diary '
    "is complete. If every day were like today you'd weigh 170 lbs in 5 weeks."
    '<a class="button complete-this-day-button" '
    'href="/food/day_incomplete?date=2026-07-08">Make Additional Entries</a>'
    "</span></div>"
)
COMPLETE_LOW_CALORIES = (
    '<div id="complete_day"><p class="starvation-mode-warning">Based on your total '
    "calories consumed for today, you are likely not eating enough.</p>"
    '<span id="complete_entry_button"><a class="button complete-this-day-button" '
    'href="/food/day_incomplete?date=2026-07-08">Make Additional Entries</a>'
    "</span></div>"
)


def with_box(diary_html, box):
    return diary_html.replace("</table>", "</table>\n" + box)


def diary_route(make_response, diary_html, before, after):
    def respond(calls):
        posted = any(
            m == "POST" and ("day_complete" in u or "day_incomplete" in u)
            for m, u, _ in calls
        )
        return make_response(text=with_box(diary_html, after if posted else before))

    return respond


@pytest.fixture
def toggles(client, make_response):
    client.session.route("POST", "food/day_complete", make_response(text="ok"))
    client.session.route("POST", "food/day_incomplete", make_response(text="ok"))
    return client


@pytest.fixture
def shows(toggles, diary_html, make_response):
    def route(box, after=None):
        page = (
            diary_route(make_response, diary_html, box, after)
            if after is not None
            else make_response(text=with_box(diary_html, box))
        )
        toggles.session.route("GET", "food/diary?date=", page)
        return toggles

    return route


def posts(client):
    return [(u, kw) for m, u, kw in client.session.calls if m == "POST"]


@pytest.mark.parametrize(
    "box, expected",
    [(INCOMPLETE, False), (COMPLETE, True), (COMPLETE_LOW_CALORIES, True), ("", None)],
)
def test_is_complete_reads_the_button(diary_html, box, expected):
    doc = lh.fromstring(with_box(diary_html, box))
    found = doc.xpath("//div[@id='complete_day']")
    assert day_completion.is_complete(found[0] if found else None) is expected


def test_complete_day_posts_with_csrf_and_confirms(shows):
    client = shows(INCOMPLETE, after=COMPLETE)
    result = day_completion.set_day_complete(client, TODAY, True)
    assert result["complete"] is True and result["changed"] is True
    assert result["message"] == (
        "Your Food Diary is complete. If every day were like today you'd weigh "
        "170 lbs in 5 weeks."
    )
    url, kwargs = posts(client)[-1]
    assert url.endswith("food/day_complete?date=2026-07-08")
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"
    assert kwargs["headers"]["Referer"].endswith("food/diary?date=2026-07-08")


def test_complete_day_is_a_no_op_when_already_complete(shows):
    client = shows(COMPLETE)
    result = day_completion.set_day_complete(client, TODAY, True)
    assert result["complete"] is True and result["changed"] is False
    assert posts(client) == []


def test_reopen_is_a_no_op_when_already_incomplete(shows):
    client = shows(INCOMPLETE)
    result = day_completion.set_day_complete(client, TODAY, False)
    assert result["complete"] is False and result["changed"] is False
    assert result["message"] is None
    assert posts(client) == []


def test_reopen_day_posts_day_incomplete(shows):
    client = shows(COMPLETE, after=INCOMPLETE)
    result = day_completion.set_day_complete(client, TODAY, False)
    assert result["complete"] is False and result["changed"] is True
    assert posts(client)[-1][0].endswith("food/day_incomplete?date=2026-07-08")


def test_complete_day_below_minimum_still_counts(shows):
    client = shows(INCOMPLETE, after=COMPLETE_LOW_CALORIES)
    result = day_completion.set_day_complete(client, TODAY, True)
    assert result["complete"] is True
    assert "not eating enough" in result["message"]
    assert "Make Additional Entries" not in result["message"]


def test_complete_day_errors_when_the_state_did_not_change(shows):
    client = shows(INCOMPLETE)
    with pytest.raises(RuntimeError, match="is still incomplete") as raised:
        day_completion.set_day_complete(client, TODAY, True)
    assert not mfp_client.is_auth_error(raised.value)


def test_reopen_errors_with_the_actual_state(shows):
    client = shows(COMPLETE)
    with pytest.raises(RuntimeError, match="is still complete"):
        day_completion.set_day_complete(client, TODAY, False)


def test_complete_day_needs_the_completion_box(toggles):
    with pytest.raises(RuntimeError, match="button on the website") as raised:
        day_completion.set_day_complete(toggles, TODAY, True)
    assert "no 'Complete This Entry' section" in str(raised.value)
    assert posts(toggles) == []


def test_report_without_a_completion_box_is_null(toggles):
    result = day_completion.day_state(toggles, TODAY)
    assert result["complete"] is None and result["message"] is None
    assert posts(toggles) == []


def test_complete_day_tool_only_reports_by_default(shows, monkeypatch):
    client = shows(INCOMPLETE)
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)
    result = asyncio.run(server.fitness_complete_day(date="2026-07-08"))
    assert result["ok"] is True
    assert result["complete"] is False and result["changed"] is False
    assert result["message"] is None
    assert posts(client) == []


def test_complete_day_tool_reports_a_completed_day(shows, monkeypatch):
    client = shows(COMPLETE)
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)
    result = asyncio.run(server.fitness_complete_day(date="2026-07-08"))
    assert result["complete"] is True and result["changed"] is False
    assert "170 lbs in 5 weeks" in result["message"]
    assert posts(client) == []


def test_complete_day_tool_completes_when_asked(shows, monkeypatch):
    client = shows(INCOMPLETE, after=COMPLETE)
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: client)
    result = asyncio.run(server.fitness_complete_day(complete=True, date="2026-07-08"))
    assert result["ok"] is True
    assert result["complete"] is True and result["changed"] is True
    assert len(posts(client)) == 1
