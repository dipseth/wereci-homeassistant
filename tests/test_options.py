"""Per-account options: which surfaces weReci adds to Home Assistant."""

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .test_todo import ENTITY, _setup


async def _save(hass: HomeAssistant, entry, **options) -> None:
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    done = await hass.config_entries.options.async_configure(
        flow["flow_id"], user_input=options
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def _views(hass: HomeAssistant, hass_ws_client) -> list[dict]:
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/config", "url_path": "wereci-cook"})
    return (await ws.receive_json())["result"]["views"]


async def test_both_surfaces_are_on_until_turned_off(
    hass: HomeAssistant, entry, list_call, hass_ws_client
) -> None:
    await _setup(hass, entry)
    assert hass.states.get(ENTITY) is not None
    # Control panel, shopping list tab, hidden display view.
    assert len(await _views(hass, hass_ws_client)) == 3

    await _save(hass, entry, shopping_list=False, control_panel=True)
    # No list, no list tab; the ingredients list is the cook's, and stays.
    control, display = await _views(hass, hass_ws_client)
    assert control["path"].startswith("control-")
    assert hass.states.get(ENTITY) is None
    assert hass.states.get(ENTITY.replace("shopping_list", "ingredients")) is not None

    await _save(hass, entry, shopping_list=False, control_panel=False)

    # The list entity is gone, not left behind as unavailable.
    assert hass.states.get(ENTITY) is None
    # The sidebar entry is hidden; the view a Cast device loads is still served.
    panel = hass.data["frontend_panels"]["wereci-cook"]
    assert panel.to_response()["show_in_sidebar"] is False
    (display,) = await _views(hass, hass_ws_client)
    assert display["path"] == f"display-{entry.entry_id.lower()}"

    await _save(hass, entry, shopping_list=True, control_panel=True)

    assert hass.states.get(ENTITY) is not None
    assert panel is not hass.data["frontend_panels"]["wereci-cook"]
    assert hass.data["frontend_panels"]["wereci-cook"].to_response()["show_in_sidebar"]
    assert len(await _views(hass, hass_ws_client)) == 3
