"""The synced list as a to-do entity."""

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from custom_components.wereci.coordinator import parse_list

from .conftest import LIST

ENTITY = "todo.wereci_cook_example_test_shopping_list"


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _items(hass: HomeAssistant) -> list[dict]:
    res = await hass.services.async_call(
        "todo", "get_items", {"entity_id": ENTITY}, blocking=True, return_response=True
    )
    return res[ENTITY]["items"]


async def test_list_becomes_todo_items(hass: HomeAssistant, entry, list_call) -> None:
    await _setup(hass, entry)
    assert hass.states.get(ENTITY).state == "1"  # one line still to get
    items = await _items(hass)
    assert [(i["uid"], i["summary"], i["status"]) for i in items] == [
        ("milk", "1 pt milk", "needs_action"),
        ("eggs", "6 eggs", "completed"),
    ]
    assert items[0]["description"] == "Dairy · Carrot soup"
    list_call.assert_awaited_with("get_shopping_list", {})
    # The Assist side registered too.
    assert any(api.id.startswith("wereci-") for api in llm.async_get_apis(hass))


async def test_tick_add_rename_delete_map_onto_the_tool(
    hass: HomeAssistant, entry, list_call
) -> None:
    await _setup(hass, entry)
    call = lambda svc, data: hass.services.async_call(  # noqa: E731
        "todo", svc, {"entity_id": ENTITY, **data}, blocking=True
    )

    await call("update_item", {"item": "milk", "status": "completed"})
    list_call.assert_awaited_with("update_shopping_list", {"check": ["milk"]})

    await call("update_item", {"item": "eggs", "status": "needs_action"})
    list_call.assert_awaited_with("update_shopping_list", {"uncheck": ["eggs"]})

    await call("add_item", {"item": "paper towels"})
    list_call.assert_awaited_with("update_shopping_list", {"add": ["paper towels"]})

    await call("update_item", {"item": "milk", "rename": "oat milk"})
    list_call.assert_awaited_with(
        "update_shopping_list", {"remove": ["milk"], "add": ["oat milk"]}
    )

    await call("remove_item", {"item": ["eggs"]})
    list_call.assert_awaited_with("update_shopping_list", {"remove": ["eggs"]})


async def test_no_synced_list_is_unavailable_with_a_reason(
    hass: HomeAssistant, entry, list_call
) -> None:
    list_call.return_value = {"available": False, "reason": "no_synced_list", "note": "…"}
    await _setup(hass, entry)
    state = hass.states.get(ENTITY)
    assert state.state == "unavailable"


async def test_a_write_that_finds_no_list_raises(
    hass: HomeAssistant, entry, list_call
) -> None:
    await _setup(hass, entry)
    list_call.return_value = {"available": False, "reason": "not_shared"}
    with pytest.raises(HomeAssistantError, match="not_shared"):
        await hass.services.async_call(
            "todo", "add_item", {"entity_id": ENTITY, "item": "x"}, blocking=True
        )


async def test_unchanged_keeps_the_previous_list(
    hass: HomeAssistant, entry, list_call
) -> None:
    await _setup(hass, entry)
    list_call.return_value = {"available": True, "seq": 7, "unchanged": True}
    await entry.runtime_data.list_coordinator.async_refresh()
    list_call.assert_awaited_with("get_shopping_list", {"since": 7})
    assert len(await _items(hass)) == 2


def test_parse_list_shapes() -> None:
    assert parse_list({"error": "permission_required"}).reason == "permission_required"
    assert parse_list({"available": False, "reason": "not_shared"}).available is False
    parsed = parse_list(LIST)
    assert parsed.seq == 7 and [i.key for i in parsed.items] == ["milk", "eggs"]
