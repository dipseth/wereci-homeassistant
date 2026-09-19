"""Cook Mode on a Home Assistant screen: HA is the display side of the relay."""

import asyncio
import json
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er, intent

SENSOR = "sensor.wereci_cook_example_test_cooking"
NEXT = "button.wereci_cook_example_test_next_step"
TOKEN = "rt-secret-token-0123456789"
SNAPSHOT = {
    "title": "Carrot soup",
    "stepIdx": 1,
    "totalSteps": 5,
    "stepText": "Sweat the onions.",
    "componentLabel": None,
    "scaleLabel": "2×",
}


class _Res:
    def __init__(self, status: int, data: dict | None = None) -> None:
        self.status_code = status
        self._data = data or {}

    def json(self) -> dict:
        return self._data

    def raise_for_status(self) -> None:
        assert self.status_code < 400


class FakeRelay:
    """The four relay calls the display side makes."""

    def __init__(self) -> None:
        self.polls: asyncio.Queue[_Res] = asyncio.Queue()
        self.commands: list[dict] = []
        self.deleted: list[str] = []

    async def post(self, url: str, json: dict | None = None, **_) -> _Res:
        if url.endswith("/channel"):
            return _Res(200, {"code": "ABCDEF", "token": TOKEN})
        self.commands.append(json)
        return _Res(200, {"ok": True})

    async def get(self, url: str, params: dict, **_) -> _Res:
        assert params["token"] == TOKEN
        return await self.polls.get()

    async def delete(self, url: str, params: dict, **_) -> _Res:
        self.deleted.append(params["token"])
        return _Res(200)


@pytest.fixture
async def relay(hass: HomeAssistant):
    fake = FakeRelay()
    with patch(
        "custom_components.wereci.cook_display.get_async_client", return_value=fake
    ):
        yield fake
        # Hang up while the relay is still the fake one; unload would
        # otherwise do it over the real network at hass teardown.
        for loaded in hass.config_entries.async_loaded_entries("wereci"):
            await loaded.runtime_data.cook_display.async_stop(restore=False)


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _cast_player(hass: HomeAssistant) -> str:
    return (
        er.async_get(hass)
        .async_get_or_create("media_player", "cast", "hub", suggested_object_id="hub")
        .entity_id
    )


async def _show(hass: HomeAssistant, **data) -> dict:
    return await hass.services.async_call(
        "wereci", "show_cook_display", data, blocking=True, return_response=True
    )


async def test_show_casts_the_view_and_hands_the_code_to_the_phone(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    shown = async_mock_service(hass, "cast", "show_lovelace_view")
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    other = async_mock_service(hass, "notify", "persistent")

    res = await _show(hass, entity_id=_cast_player(hass))

    assert res == {"code": "ABCDEF", "link": "https://wereci.xyz/?cast=ABCDEF"}
    assert shown[0].data == {
        "entity_id": "media_player.hub",
        "dashboard_path": "wereci-cook",
        "view_path": f"display-{entry.entry_id.lower()}",
    }
    assert phone[0].data["data"]["url"] == "https://wereci.xyz/?cast=ABCDEF"
    assert not other  # only mobile apps by default
    state = hass.states.get(SENSOR)
    assert state.state == "waiting"
    assert state.attributes["code"] == "ABCDEF"
    assert hass.states.get(NEXT).state == "unavailable"
    # The token is on no entity.
    assert TOKEN not in json.dumps(state.attributes)


async def test_snapshot_feeds_the_sensor_and_buttons_send_intent(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    await _show(hass, entity_id=_cast_player(hass), notify=[])

    relay.polls.put_nowait(
        _Res(200, {"version": 1, "paired": True, "state": json.dumps(SNAPSHOT)})
    )
    await asyncio.sleep(0)
    await hass.async_block_till_done()

    state = hass.states.get(SENSOR)
    assert state.state == "cooking"
    assert state.attributes["title"] == "Carrot soup"
    assert (state.attributes["step"], state.attributes["total_steps"]) == (2, 5)
    assert "code" not in state.attributes  # spent

    await hass.services.async_call(
        "button", "press", {"entity_id": NEXT}, blocking=True
    )
    await intent.async_handle(hass, "test", "WereciPreviousStep")
    assert relay.commands == [
        {"token": TOKEN, "cmd": {"do": "step", "delta": 1}},
        {"token": TOKEN, "cmd": {"do": "step", "delta": -1}},
    ]


async def test_phone_hanging_up_ends_the_session_and_frees_the_screen(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    off = async_mock_service(hass, "media_player", "turn_off")
    await _show(hass, entity_id=_cast_player(hass), notify=[])

    relay.polls.put_nowait(_Res(404, {"gone": True}))
    await asyncio.sleep(0)
    await hass.async_block_till_done()

    assert hass.states.get(SENSOR).state == "idle"
    assert off[0].data["entity_id"] == "media_player.hub"


async def test_stop_closes_the_channel(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "browser_mod", "navigate")
    await _show(hass, browser_id="kitchen-tablet", notify=[], return_path="/home")
    nav = async_mock_service(hass, "browser_mod", "navigate")

    await hass.services.async_call("wereci", "stop_cook_display", {}, blocking=True)
    await hass.async_block_till_done()

    assert relay.deleted == [TOKEN]
    assert nav[0].data == {"browser_id": "kitchen-tablet", "path": "/home"}
    assert hass.states.get(SENSOR).state == "idle"


async def test_a_target_we_cannot_drive_is_refused(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    with pytest.raises(ServiceValidationError):
        await _show(hass, entity_id="media_player.sonos")
    with pytest.raises(ServiceValidationError):
        await _show(hass)


async def test_display_path_redirects_only_while_a_session_is_open(
    hass: HomeAssistant, entry, list_call, relay, hass_client_no_auth
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    client = await hass_client_no_auth()
    path = hass.states.get(SENSOR).attributes["display_path"]

    idle = await client.get(path, allow_redirects=False)
    assert idle.status == 200

    await _show(hass, entity_id=_cast_player(hass), notify=[])
    live = await client.get(path, allow_redirects=False)
    assert live.status == 302
    assert live.headers["Location"] == (
        f"https://wereci.xyz/cast?rt={TOKEN}&code=ABCDEF"
    )
    wrong = await client.get("/api/wereci/display/nope", allow_redirects=False)
    assert wrong.status == 200


async def test_the_dashboard_comes_with_the_integration(
    hass: HomeAssistant, entry, list_call, relay, hass_ws_client
) -> None:
    """Nobody builds a dashboard by hand: the view show_cook_display targets exists."""
    hass.config.external_url = "https://ha.example.test"
    await _setup(hass, entry)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/config", "url_path": "wereci-cook"})
    config = (await ws.receive_json())["result"]

    path = hass.states.get(SENSOR).attributes["display_path"]
    assert config["views"] == [
        {
            "path": f"display-{entry.entry_id.lower()}",
            "title": "Cook display (cook@example.test)",
            "panel": True,
            "cards": [
                {
                    "type": "iframe",
                    # Absolute: HA's Cast receiver lives on another origin.
                    "url": f"https://ha.example.test{path}",
                    "aspect_ratio": "56%",
                }
            ],
        }
    ]
    panel = hass.data["frontend_panels"]["wereci-cook"]
    assert panel.config == {"mode": "yaml"}
    assert panel.to_response()["show_in_sidebar"] is False


async def test_a_recipe_makes_the_link_open_cook_mode_on_it(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    player = _cast_player(hass)
    link = "https://wereci.xyz/recipe/r-2?cook=1&cast=ABCDEF"

    # By name: the first hit that is a recipe, not an encyclopedia card.
    list_call.return_value = {
        "hits": [
            {"id": "r-1", "title": "Carrot", "reference": True},
            {"id": "r-2", "title": "Carrot soup"},
        ]
    }
    res = await _show(hass, entity_id=player, recipe="carrot soup")
    list_call.assert_awaited_with("search_recipes", {"query": "carrot soup", "limit": 5})
    assert res == {
        "code": "ABCDEF", "link": link, "recipe_id": "r-2", "title": "Carrot soup"
    }
    assert phone[0].data["data"]["url"] == link
    assert "Carrot soup" in phone[0].data["message"]

    # By id: checked against weReci before anything is put on a screen.
    list_call.return_value = {"title": "Carrot soup"}
    assert (await _show(hass, entity_id=player, recipe_id="r-2"))["link"] == link
    list_call.return_value = {"error": "not_found"}
    with pytest.raises(ServiceValidationError):
        await _show(hass, entity_id=player, recipe_id="nope")
    list_call.return_value = {"hits": []}
    with pytest.raises(ServiceValidationError):
        await _show(hass, entity_id=player, recipe="zzz")
