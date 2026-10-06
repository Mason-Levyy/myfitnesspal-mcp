import asyncio
import datetime

import pytest
from conftest import FakeResponse

from myfitnesspal_mcp import day_completion, mfp_client, server
from myfitnesspal_mcp.store import Store

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
# Completing a day under MFP's calorie minimum shows a warning first; the day is
# still complete (the button offers "Make Additional Entries").
COMPLETE_LOW_CALORIES = (
    '<div id="complete_day"><p class="starvation-mode-warning">Based on your total '
    "calories consumed for today, you are likely not eating enough.</p>"
    '<span id="complete_entry_button"><a class="button complete-this-day-button" '
    'href="/food/day_incomplete?date=2026-07-08">Make Additional Entries</a>'
    "</span></div>"
)


def with_box(diary_html, box):
    return diary_html.replace("</table>", "</table>\n" + box)


def diary_route(diary_html, before, after):
    """The diary page shows `before` until a day_complete/day_incomplete POST."""

    def respond(calls):
        posted = any(
            m == "POST" and ("day_complete" in u or "day_incomplete" in u)
            for m, u, _ in calls
        )
        return FakeResponse(text=with_box(diary_html, after if posted else before))

    return respond


@pytest.fixture
def toggles(client):
    client.session.route("POST", "food/day_complete", FakeResponse(text="ok"))
    client.session.route("POST", "food/day_incomplete", FakeResponse(text="ok"))
    return client


def posts(client):
    return [(u, kw) for m, u, kw in client.session.calls if m == "POST"]


@pytest.mark.parametrize(
    "box, expected",
    [(INCOMPLETE, False), (COMPLETE, True), (COMPLETE_LOW_CALORIES, True), ("", None)],
)
def test_day_completion_reads_the_button(diary_html, box, expected):
    from lxml import html as lh

    assert (
        day_completion.day_completion(lh.fromstring(with_box(diary_html, box)))
        is expected
    )


def test_complete_day_posts_with_csrf_and_confirms(toggles, diary_html):
    toggles.session.route(
        "GET", "food/diary?date=", diary_route(diary_html, INCOMPLETE, COMPLETE)
    )
    result = day_completion.set_day_complete(toggles, TODAY, True)
    assert result["complete"] is True and result["changed"] is True
    assert "170 lbs in 5 weeks" in result["message"]
    url, kwargs = posts(toggles)[-1]
    assert url.endswith("food/day_complete?date=2026-07-08")
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"
    assert kwargs["headers"]["Referer"].endswith("food/diary?date=2026-07-08")


def test_complete_day_is_a_no_op_when_already_complete(toggles, diary_html):
    toggles.session.route(
        "GET", "food/diary?date=", FakeResponse(text=with_box(diary_html, COMPLETE))
    )
    result = day_completion.set_day_complete(toggles, TODAY, True)
    assert result == {
        "day": "2026-07-08",
        "complete": True,
        "changed": False,
        "message": result["message"],
    }
    assert posts(toggles) == []


def test_reopen_day_posts_day_incomplete(toggles, diary_html):
    toggles.session.route(
        "GET", "food/diary?date=", diary_route(diary_html, COMPLETE, INCOMPLETE)
    )
    result = day_completion.set_day_complete(toggles, TODAY, False)
    assert result["complete"] is False and result["changed"] is True
    assert posts(toggles)[-1][0].endswith("food/day_incomplete?date=2026-07-08")


def test_complete_day_below_minimum_still_counts(toggles, diary_html):
    toggles.session.route(
        "GET",
        "food/diary?date=",
        diary_route(diary_html, INCOMPLETE, COMPLETE_LOW_CALORIES),
    )
    result = day_completion.set_day_complete(toggles, TODAY, True)
    assert result["complete"] is True
    assert "not eating enough" in result["message"]


def test_complete_day_errors_when_the_state_did_not_change(toggles, diary_html):
    toggles.session.route(
        "GET", "food/diary?date=", FakeResponse(text=with_box(diary_html, INCOMPLETE))
    )
    with pytest.raises(RuntimeError, match="still not complete"):
        day_completion.set_day_complete(toggles, TODAY, True)


def test_complete_day_needs_the_completion_box(toggles, diary_html):
    with pytest.raises(RuntimeError, match="no 'Complete This Entry' section"):
        day_completion.set_day_complete(toggles, TODAY, True)
    assert posts(toggles) == []


def test_day_status_only(toggles, diary_html):
    toggles.session.route(
        "GET", "food/diary?date=", FakeResponse(text=with_box(diary_html, INCOMPLETE))
    )
    result = day_completion.set_day_complete(toggles, TODAY, None)
    assert result["complete"] is False and result["changed"] is False
    assert posts(toggles) == []


def test_still_incomplete_error_never_triggers_a_session_refresh():
    error = RuntimeError(
        "MyFitnessPal accepted the request but 2026-07-08 is still not complete"
    )
    assert not mfp_client.is_auth_error(error)


def test_complete_day_tool_caches_the_state(toggles, diary_html, tmp_path, monkeypatch):
    toggles.session.route(
        "GET", "food/diary?date=", diary_route(diary_html, INCOMPLETE, COMPLETE)
    )
    store = Store(tmp_path / "cache.db")
    monkeypatch.setattr(server, "_store", store)
    monkeypatch.setattr(server.mfp_client, "get_client", lambda: toggles)
    result = asyncio.run(server.fitness_complete_day(date="2026-07-08"))
    assert result["ok"] is True and result["complete"] is True
    assert store.day_record("2026-07-08")["complete"] is True


def test_client_reads_a_low_calorie_completed_day_as_complete(diary_html):
    from lxml import html as lh

    doc = lh.fromstring(with_box(diary_html, COMPLETE_LOW_CALORIES))
    assert mfp_client.CurlCffiClient._get_completion(None, doc) is True
