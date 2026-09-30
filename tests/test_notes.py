import datetime

from myfitnesspal_mcp import notes

TODAY = datetime.date(2026, 7, 8)


def test_get_note_double_unescapes_body(client, make_response):
    client.session.route(
        "GET", "food/note", make_response(json_data={"item": {"body": "a &amp;amp; b"}})
    )
    assert notes.get_note(client, TODAY) == "a & b"
    method, url, kwargs = client.session.calls[-1]
    assert method == "GET"
    assert "food/note?date=2026-07-08" in url


def test_get_note_empty_is_none(client, make_response):
    client.session.route(
        "GET", "food/note", make_response(json_data={"item": {"body": ""}})
    )
    assert notes.get_note(client, TODAY) is None


def test_set_note_posts_form_body_and_csrf(client):
    result = notes.set_note(client, TODAY, "today test\n")
    assert result == {"day": "2026-07-08", "note": "today test\n"}
    method, url, kwargs = client.session.calls[-1]
    assert method == "POST"
    assert "food/note" in url
    assert kwargs["data"] == {"body": "today test\n", "date": "2026-07-08"}
    assert kwargs["headers"]["X-CSRF-Token"] == "DIARYTOKEN"
    assert kwargs["headers"]["Content-Type"].startswith(
        "application/x-www-form-urlencoded"
    )


def test_push_note_append_keeps_existing(client, make_response):
    client.session.route(
        "GET", "food/note", make_response(json_data={"item": {"body": "line one"}})
    )
    result = notes.push_note(client, TODAY, "line two", append=True)
    assert result["note"] == "line one\nline two"
    method, url, kwargs = client.session.calls[-1]
    assert kwargs["data"]["body"] == "line one\nline two"
