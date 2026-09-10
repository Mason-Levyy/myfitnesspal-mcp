import datetime

from myfitnesspal_mcp import diary

TODAY = datetime.date(2026, 9, 10)


def test_exercise_entries_parse_names_ids_minutes_calories(exercise_client):
    entries = diary.exercise_entries(exercise_client, TODAY)
    assert [(e["entry_id"], e["name"]) for e in entries] == [
        ("111", "Aerobics, general"),
        ("222", "Running (jogging), 8 kph"),
        ("333", "Aerobics, general"),
    ]
    assert entries[0]["minutes"] == 1
    assert entries[0]["calories"] == 13
    assert entries[1]["minutes"] == 20
    assert entries[1]["calories"] == 201


def test_exercise_entry_id_strips_query_string(exercise_client):
    entries = diary.exercise_entries(exercise_client, TODAY)
    assert entries[2]["entry_id"] == "333"


def test_delete_exercise_removes_all_matches(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "aerobics")
    assert result["count"] == 2
    assert result["day"] == TODAY.isoformat()
    assert [r["entry_id"] for r in result["removed"]] == ["111", "333"]
    assert result["removed"][1]["calories"] == 25

    posts = [c for c in exercise_client.session.calls if c[0] == "POST"]
    assert len(posts) == 2
    assert all("exercise/remove/" in c[1] for c in posts)
    assert all(c[2]["data"]["_method"] == "delete" for c in posts)
    assert all(c[2]["data"]["authenticity_token"] == "EXERCISETOKEN" for c in posts)


def test_delete_exercise_no_matches_leaves_diary_untouched(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "swimming")
    assert result == {"day": TODAY.isoformat(), "removed": [], "count": 0}
    posts = [c for c in exercise_client.session.calls if c[0] == "POST"]
    assert posts == []


def test_delete_exercise_is_case_insensitive(exercise_client):
    result = diary.delete_exercise(exercise_client, TODAY, "RUNNING")
    assert result["count"] == 1
    assert result["removed"][0]["entry_id"] == "222"
