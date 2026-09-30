from datetime import date
from urllib import parse

from lxml import html as lh

ORIGIN = "https://www.myfitnesspal.com"
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"


def site_url(client, path: str, day: date | None = None) -> str:
    url = parse.urljoin(client.BASE_URL_SECURE, path)
    if day is None:
        return url
    return f"{url}?date={day.isoformat()}"


def api_headers(client, extra: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {client.access_token}",
        "mfp-client-id": "mfp-main-js",
        "mfp-user-id": str(client.user_id),
        "X-Requested-With": "XMLHttpRequest",
    }
    if extra:
        headers.update(extra)
    return headers


def form_headers(
    client, csrf: str, referer: str, accept: str | None = None
) -> dict[str, str]:
    extra = {
        "X-CSRF-Token": csrf,
        "Origin": ORIGIN,
        "Referer": referer,
        "Content-Type": FORM_CONTENT_TYPE,
    }
    if accept:
        extra["Accept"] = accept
    return api_headers(client, extra)


def require_status(resp, endpoint: str, accepted: tuple[int, ...]) -> None:
    if resp.status_code not in accepted:
        raise RuntimeError(f"MyFitnessPal {endpoint} returned HTTP {resp.status_code}")


def get_document(client, url: str) -> lh.HtmlElement:
    resp = client.session.get(url, headers=api_headers(client))
    resp.raise_for_status()
    return lh.fromstring(resp.text)


def get_page(client, url: str) -> tuple[lh.HtmlElement, str]:
    doc = get_document(client, url)
    tokens = doc.xpath("//meta[@name='csrf-token']/@content")
    if not tokens:
        raise RuntimeError("couldn't read the MyFitnessPal csrf token")
    return doc, tokens[0]


def get_json(client, url: str):
    resp = client.session.get(
        url, headers=api_headers(client, {"Accept": "application/json"})
    )
    resp.raise_for_status()
    return resp.json()


def post_legacy_delete(
    client, path: str, entry_id: str, token: str, *, referer: str
) -> None:
    resp = client.session.post(
        site_url(client, f"{path}/{entry_id}"),
        data={"_method": "delete", "authenticity_token": token},
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": ORIGIN,
            "Referer": referer,
        },
    )
    require_status(resp, f"/{path}", (200, 204))
