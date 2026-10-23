from .custom_items import (
    KINDS,
    NoSuchItem,
    items_with_details,
    pick,
    single_kind,
    tab_rows,
)
from .mfp_web import api_url, json_headers, require_status, site_url


def _delete_request(client, plural: str, item: dict):
    if plural == "foods":
        url = api_url(client, f"foods/{item['id']}")
        return client.session.delete(url, headers=json_headers(client))
    if plural == "recipes":
        url = api_url(client, f"recipes/{item['recipe_id']}")
        return client.session.delete(url, headers=json_headers(client))
    csrf = client.session.get(site_url(client, "api/auth/csrf")).json()["csrfToken"]
    url = site_url(client, f"api/services/users/meals/delete/{item['meal_id']}")
    return client.session.delete(
        url, headers={"Accept": "application/json", "X-CSRF-Token": csrf}
    )


def delete_custom(client, name: str, kind: str) -> dict:
    plural = single_kind(kind)
    items, problem = items_with_details(client, plural, tab_rows(client, plural))
    item = pick(items, name, exact_only=True)
    if item[KINDS[plural].id_field] is None:
        raise NoSuchItem(
            f"couldn't find the MyFitnessPal id of {item['name']!r}, so nothing "
            f"was deleted ({problem or 'it is missing from the detail list'})"
        )
    require_status(_delete_request(client, plural, item), "delete", (200, 204))
    return {"deleted": item["name"], "kind": KINDS[plural].singular}
