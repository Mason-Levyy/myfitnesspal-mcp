import datetime

from myfitnesspal_mcp import measurements

TODAY = datetime.date(2026, 7, 8)


def test_set_weight_posts_v2_items(client, make_response):
    client.session.route(
        "POST",
        "v2/measurements",
        make_response(
            status_code=200,
            json_data={
                "items": [
                    {
                        "type": "Weight",
                        "value": 175.0,
                        "date": "2026-07-08",
                        "unit": "pounds",
                    }
                ]
            },
        ),
    )
    result = measurements.set_weight(client, TODAY, 175.0)
    assert result == {"day": "2026-07-08", "weight": 175.0, "unit": "pounds"}
    method, url, kwargs = client.session.calls[-1]
    assert "v2/measurements" in url
    assert kwargs["json"] == {
        "items": [{"type": "Weight", "value": 175.0, "date": "2026-07-08"}]
    }
