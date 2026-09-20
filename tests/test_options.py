"""Per-account options: which pages the weReci dashboard shows.

Both boxes are pages and nothing else. The to-do entities and the list sync do
not depend on them, and either page stands without the other.
"""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .test_todo import ENTITY, _setup

INGREDIENTS = ENTITY.replace("shopping_list", "ingredients")


async def _save(hass: HomeAssistant, entry, **options) -> None:
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    done = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input=options
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def _pages(hass: HomeAssistant, hass_ws_client) -> list[str]:
    """The views the dashboard serves, by the kind each path starts with."""
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/config", "url_path": "wereci-cook"})
    views = (await ws.receive_json())["result"]["views"]
    return [view["path"].split("-")[0] for view in views]


def _in_sidebar(hass: HomeAssistant) -> bool:
    return hass.data["frontend_panels"]["wereci-cook"].to_response()["show_in_sidebar"]


async def test_both_pages_are_on_until_turned_off(
    hass: HomeAssistant, entry, list_call, hass_ws_client
) -> None:
    await _setup(hass, entry)
    # Cook page, shopping list page, hidden display view.
    assert await _pages(hass, hass_ws_client) == ["control", "list", "display"]
    assert _in_sidebar(hass) is True


async def test_either_page_stands_without_the_other(
    hass: HomeAssistant, entry, list_call, hass_ws_client
) -> None:
    await _setup(hass, entry)

    await _save(hass, entry, shopping_list=False, control_panel=True)
    assert await _pages(hass, hass_ws_client) == ["control", "display"]
    assert _in_sidebar(hass) is True

    # The list on its own — what the old nesting made unreachable.
    await _save(hass, entry, shopping_list=True, control_panel=False)
    assert await _pages(hass, hass_ws_client) == ["list", "display"]
    assert _in_sidebar(hass) is True


async def test_neither_page_leaves_the_sidebar_but_still_casts(
    hass: HomeAssistant, entry, list_call, hass_ws_client
) -> None:
    await _setup(hass, entry)
    await _save(hass, entry, shopping_list=False, control_panel=False)

    # Nothing to show a tab for; the view a Cast device loads is still served.
    assert await _pages(hass, hass_ws_client) == ["display"]
    assert _in_sidebar(hass) is False

    await _save(hass, entry, shopping_list=True, control_panel=True)
    assert await _pages(hass, hass_ws_client) == ["control", "list", "display"]
    assert _in_sidebar(hass) is True


async def test_the_entities_ignore_the_page_options(
    hass: HomeAssistant, entry, list_call, hass_ws_client
) -> None:
    """The checkboxes pick pages, not features: both lists live on regardless."""
    await _setup(hass, entry)
    assert hass.states.get(ENTITY) is not None
    assert hass.states.get(INGREDIENTS) is not None

    await _save(hass, entry, shopping_list=False, control_panel=False)

    assert hass.states.get(ENTITY) is not None
    assert hass.states.get(INGREDIENTS) is not None
    # The list is still being synced, not just present and stale.
    list_call.assert_awaited_with("get_shopping_list", {})
