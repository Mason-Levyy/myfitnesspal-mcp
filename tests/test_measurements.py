import datetime
import json
from pathlib import Path

from lxml import html as lh

from myfitnesspal_mcp import measurements

TODAY = datetime.date(2026, 7, 8)
FIXTURES = Path(__file__).parent / "fixtures"


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


def next_data_page(items):
    data = {
        "queryKey": ["measurements", "Weight", 1],
        "state": {"data": {"items": items}},
    }
    payload = {"props": {"pageProps": {"dehydratedState": {"queries": [data]}}}}
    return f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>'


def test_page_weights_skip_empty_weigh_ins():
    doc = lh.fromstring((FIXTURES / "measurements.html").read_text())
    assert measurements.page_weights(doc) == {
        datetime.date(2026, 7, 8): 80.4,
        datetime.date(2026, 7, 5): 80.9,
        datetime.date(2026, 6, 28): 81.6,
    }


def test_weight_history_pages_back_until_the_start(client, make_response):
    older = [{"date": "2026-06-20", "value": 82.0}, {"date": "2026-06-01", "value": 83}]
    client.session.route(
        "GET",
        "measurements/edit?page=1&type=Weight",
        make_response(text=(FIXTURES / "measurements.html").read_text()),
    )
    client.session.route(
        "GET",
        "measurements/edit?page=2&type=Weight",
        make_response(text=next_data_page(older)),
    )
    since = datetime.date(2026, 6, 10)
    history = measurements.weight_history(
        client, since, until=datetime.date(2026, 7, 6)
    )
    assert history == {
        datetime.date(2026, 7, 5): 80.9,
        datetime.date(2026, 6, 28): 81.6,
        datetime.date(2026, 6, 20): 82.0,
    }
    pages = [url for _, url, _ in client.session.calls]
    assert len(pages) == 2


def test_weight_history_stops_on_an_empty_page(client, make_response):
    client.session.route(
        "GET", "measurements/edit", make_response(text=next_data_page([]))
    )
    assert measurements.weight_history(client, datetime.date(2026, 1, 1), TODAY) == {}
    assert len(client.session.calls) == 1
