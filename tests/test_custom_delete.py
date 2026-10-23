import pytest
from conftest import FakeResponse, my_food, my_foods_tab

from myfitnesspal_mcp import custom_delete, custom_items, mfp_web


def deletes(client):
    return [c for c in client.session.calls if c[0] == "DELETE"]


def test_delete_custom_deletes_only_an_exact_name(library):
    library.session.route("DELETE", "v2/recipes/7001", FakeResponse(status_code=204))
    assert custom_delete.delete_custom(library, "turkey chili", "recipe") == {
        "deleted": "Turkey Chili",
        "kind": "recipe",
    }
    with pytest.raises(custom_items.NoSuchItem, match="exact name"):
        custom_delete.delete_custom(library, "Turkey", "recipe")
    assert len(deletes(library)) == 1


def test_delete_custom_deletes_a_saved_meal_with_the_csrf_token(library):
    library.session.route(
        "GET", "api/auth/csrf", FakeResponse(json_data={"csrfToken": "NEXTAUTH"})
    )
    library.session.route(
        "DELETE", "api/services/users/meals/delete/42", FakeResponse(status_code=200)
    )
    custom_delete.delete_custom(library, "Morning smoothie", "meal")
    _, _, kwargs = deletes(library)[-1]
    assert kwargs["headers"]["X-CSRF-Token"] == "NEXTAUTH"


def test_delete_custom_deletes_the_unbranded_food_not_its_branded_namesake(library):
    library.session.route(
        "GET",
        "food/load_my_foods",
        FakeResponse(text=my_foods_tab("Oats", "Quaker - Oats")),
    )
    library.session.route(
        "GET",
        "api/services/users/foods/mine",
        FakeResponse(
            json_data=[
                my_food("plain", "", "Oats", 100),
                my_food("quaker", "Quaker", "Oats", 150),
            ]
        ),
    )
    library.session.route("DELETE", "v2/foods/", FakeResponse(status_code=204))
    custom_delete.delete_custom(library, "Oats", "food")
    _, url, _ = deletes(library)[-1]
    assert url == "https://api.myfitnesspal.com/v2/foods/plain"


def test_delete_custom_refuses_items_that_share_a_name(library):
    library.session.route(
        "GET", "food/load_my_foods", FakeResponse(text=my_foods_tab("Oats", "Oats"))
    )
    with pytest.raises(custom_items.NoSuchItem, match="several items are named"):
        custom_delete.delete_custom(library, "Oats", "food")
    assert not deletes(library)


def test_delete_custom_refuses_an_item_whose_id_is_unknown(library):
    library.session.route(
        "GET", "food/load_my_foods", FakeResponse(text=my_foods_tab("Protein Waffle"))
    )
    with pytest.raises(custom_items.NoSuchItem, match="nothing was deleted"):
        custom_delete.delete_custom(library, "Protein Waffle", "food")
    assert not deletes(library)


def test_delete_custom_refuses_when_details_fail(library, monkeypatch):
    monkeypatch.setattr(mfp_web.time, "sleep", lambda seconds: None)
    library.session.route("GET", "v2/recipes", FakeResponse(status_code=503))
    with pytest.raises(custom_items.NoSuchItem, match="recipes details unavailable"):
        custom_delete.delete_custom(library, "Turkey Chili", "recipe")
    assert not deletes(library)


def test_delete_custom_needs_one_kind(library):
    with pytest.raises(ValueError, match="kind must be one of: food, meal, recipe"):
        custom_delete.delete_custom(library, "Turkey Chili", "all")
