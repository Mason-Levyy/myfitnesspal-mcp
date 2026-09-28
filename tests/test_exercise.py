import datetime

import pytest
from lxml import html as lh

from myfitnesspal_mcp import diary

TODAY = datetime.date(2026, 9, 10)


@pytest.fixture
def exercise_doc(exercise_html):
    return lh.fromstring(exercise_html)


def _posts(client):
    return [call for call in client.session.calls if call[0] == "POST"]


def test_exercise_page_uses_username_url_and_reads_token(exercise_client):
    _, token = diary.exercise_page(exercise_client, TODAY)
    assert token == "EXERCISETOKEN"
    _, url, _ = exercise_client.session.calls[-1]
    assert url.endswith("exercise/diary/tester?date=2026-09-10")


def test_exercise_entries_parse_cardio_rows(exercise_doc):
    cardio = [
        e
        for e in diary.exercise_entries(exercise_doc)
        if e["section"] == "cardiovascular"
    ]
    assert cardio == [
        {
            "entry_id": "111",
            "section": "cardiovascular",
            "name": "Aerobics, general",
            "minutes": 1.0,
            "calories_burned": 13.0,
        },
        {
            "entry_id": "222",
            "section": "cardiovascular",
            "name": "Running (jogging), 8 kph",
            "minutes": 20.0,
            "calories_burned": 1201.0,
        },
        {
            "entry_id": "333",
            "section": "cardiovascular",
            "name": "Aerobics, general",
            "minutes": 4.0,
            "calories_burned": 25.0,
        },
    ]


def test_exercise_entries_parse_strength_rows_with_their_own_columns(exercise_doc):
    strength = [
        e
        for e in diary.exercise_entries(exercise_doc)
        if e["section"] == "strength training"
    ]
    assert strength == [
        {
            "entry_id": "444",
            "section": "strength training",
            "name": "Bench Press, Barbell",
            "sets": 3.0,
            "reps_set": 8.0,
            "weight_set": 135.0,
        },
        {
            "entry_id": "555",
            "section": "strength training",
            "name": "Pull Up",
            "sets": 4.0,
            "reps_set": 10.0,
            "weight_set": None,
        },
    ]


def test_exercise_entries_skip_total_rows(exercise_doc):
    names = [e["name"] for e in diary.exercise_entries(exercise_doc)]
    assert not any("Total" in name for name in names)


def test_delete_exercise_removes_single_exact_match(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "running (jogging), 8 kph")
    assert result["count"] == 1
    assert result["removed"][0]["entry_id"] == "222"
    posts = _posts(exercise_client)
    assert len(posts) == 1
    _, url, kwargs = posts[0]
    assert url.endswith("exercise/remove/222")
    assert kwargs["data"] == {
        "_method": "delete",
        "authenticity_token": "EXERCISETOKEN",
    }
    assert kwargs["headers"]["Referer"].endswith("exercise/diary/tester")


def test_delete_exercise_strength_entry(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "bench")
    assert [r["entry_id"] for r in result["removed"]] == ["444"]


def test_delete_exercise_ambiguous_by_default(exercise_client):
    with pytest.raises(diary.AmbiguousEntry):
        diary.delete_exercise(exercise_client, TODAY, "aerobics")
    assert _posts(exercise_client) == []


def test_delete_exercise_all_matches_removes_every_match(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "aerobics", all_matches=True)
    assert result["count"] == 2
    assert result["day"] == TODAY.isoformat()
    assert [r["entry_id"] for r in result["removed"]] == ["111", "333"]
    assert [call[1].rsplit("/", 1)[-1] for call in _posts(exercise_client)] == [
        "111",
        "333",
    ]


@pytest.mark.parametrize("all_matches", [False, True])
def test_delete_exercise_no_match_lists_logged_entries(exercise_client, all_matches):
    with pytest.raises(diary.NoMatchingEntry, match="Running \\(jogging\\)"):
        diary.delete_exercise(
            exercise_client, TODAY, "swimming", all_matches=all_matches
        )
    assert _posts(exercise_client) == []


def test_delete_exercise_is_case_insensitive(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "PULL UP")
    assert result["removed"][0]["entry_id"] == "555"
