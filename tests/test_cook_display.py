"""Cook Mode on a Home Assistant screen: HA is the display side of the relay."""

import asyncio
import json
import json as jsonlib
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er, intent

from custom_components.wereci.api import WereciError

SENSOR = "sensor.wereci_cook_example_test_cooking"
IMAGE = "image.wereci_cook_example_test_cooking"
NEXT = "button.wereci_cook_example_test_next_step"
START = "button.wereci_cook_example_test_start_cooking"
RECIPE_BOX = "text.wereci_cook_example_test_recipe"
SCREEN = "select.wereci_cook_example_test_screen"
MATCHES = "select.wereci_cook_example_test_matches"
NO_PHONE = "switch.wereci_cook_example_test_without_a_phone"
TOKEN = "rt-secret-token-0123456789"
SENDER = "sender-token-0123456789"
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
        self.pushed: list[dict] = []
        self.to_sender: asyncio.Queue[_Res] = asyncio.Queue()
        self.deleted: list[str] = []

    async def post(self, url: str, json: dict | None = None, **_) -> _Res:
        if url.endswith("/channel"):
            return _Res(200, {"code": "ABCDEF", "token": TOKEN})
        if url.endswith("/pair"):
            assert json == {"code": "ABCDEF"}
            return _Res(200, {"token": SENDER})
        if url.endswith("/state"):
            # What a sender pushes is what the display side then reads.
            assert json["token"] == SENDER
            self.pushed.append(jsonlib.loads(json["state"]))
            self.polls.put_nowait(
                _Res(200, {"version": len(self.pushed), "paired": True,
                           "state": json["state"]})
            )
            return _Res(200, {"ok": True})
        self.commands.append(json)
        self.to_sender.put_nowait(
            _Res(200, {"version": len(self.commands),
                       "commands": [{"v": len(self.commands), "cmd": json["cmd"]}]})
        )
        return _Res(200, {"ok": True})

    async def get(self, url: str, params: dict, **_) -> _Res:
        if url.endswith("/command"):
            assert params["token"] == SENDER
            return await self.to_sender.get()
        assert params["token"] == TOKEN
        return await self.polls.get()

    async def delete(self, url: str, params: dict, **_) -> _Res:
        self.deleted.append(params["token"])
        return _Res(200)


@pytest.fixture
async def relay(hass: HomeAssistant):
    fake = FakeRelay()
    with (
        patch("custom_components.wereci.cook_display.get_async_client", return_value=fake),
        patch("custom_components.wereci.sender.get_async_client", return_value=fake),
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
    hass: HomeAssistant, entry, list_call, relay, hass_ws_client, hass_client
) -> None:
    """Nobody builds a dashboard by hand: the view show_cook_display targets exists."""
    hass.config.external_url = "https://ha.example.test"
    # The frontend itself is not installed here; stand in for its URL list.
    hass.data[frontend.DATA_EXTRA_MODULE_URL] = frontend.UrlManager(lambda *_: None, [])
    await _setup(hass, entry)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "lovelace/config", "url_path": "wereci-cook"})
    config = (await ws.receive_json())["result"]

    path = hass.states.get(SENSOR).attributes["display_path"]
    control, display = config["views"]
    assert display == {
        "path": f"display-{entry.entry_id.lower()}",
        "title": "Cook display (cook@example.test)",
        "visible": False,
        "panel": True,
        # Absolute: HA's Cast receiver lives on another origin.
        "cards": [
            {"type": "iframe", "url": f"https://ha.example.test{path}",
             "aspect_ratio": "56%"}
        ],
    }
    # The control panel drives the real entities.
    glance = control["cards"][1]
    assert (glance["type"], glance["image_entity"]) == ("picture-glance", IMAGE)
    assert glance["entities"][2]["tap_action"]["target"] == {"entity_id": NEXT}
    assert glance["entities"][3]["tap_action"]["perform_action"] == "wereci.stop_cook_display"
    # The picture takes no receiver taps, so it opens the view that does.
    assert glance["tap_action"]["navigation_path"] == f"/wereci-cook/{display['path']}"
    assert not [c for c in control["cards"] if c["type"] == "iframe"]
    assert SENSOR in control["cards"][2]["content"]
    assert control["cards"][0]["entities"] == [RECIPE_BOX, MATCHES, SCREEN, NO_PHONE, START]
    panel = hass.data["frontend_panels"]["wereci-cook"]
    assert panel.config == {"mode": "yaml"}
    assert panel.to_response()["show_in_sidebar"] is True
    # The sidebar carries the weReci mark, from the icon set the integration serves.
    assert panel.sidebar_icon == "wereci:mark"
    extra = hass.data[frontend.DATA_EXTRA_MODULE_URL].urls
    (url,) = [u for u in extra if u.startswith("/wereci_static/icons.js?v=")]
    served = await (await hass_client()).get(url)
    assert served.status == 200
    assert "window.customIcons.wereci" in await served.text()


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


RECIPE = {
    "recipe_title": "Carrot soup",
    "primary_photo_url": "https://wereci.xyz/p.webp",
    "instructions": ["Sweat the onions.", "Add carrots.", "Blend."],
    "ingredients": ["1 onion", "500 g carrots"],
}


async def _settle(hass: HomeAssistant) -> None:
    for _ in range(4):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


async def test_without_phone_home_assistant_runs_the_cook(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    list_call.return_value = RECIPE

    await _show(hass, entity_id=_cast_player(hass), recipe_id="r-2", without_phone=True)
    await _settle(hass)

    assert not phone  # nobody to notify
    first = relay.pushed[0]
    assert (first["title"], first["stepIdx"], first["stepText"]) == (
        "Carrot soup", 0, "Sweat the onions."
    )
    # Scaling and swaps are weReci's tools; no saved breakdown → no toggle.
    assert first["controls"] == {"scale": True, "swap": True, "breakdown": False}
    state = hass.states.get(SENSOR)
    assert (state.state, state.attributes["driven_by"]) == ("cooking", "home_assistant")

    # The button's intent comes back round the relay and HA answers it.
    await hass.services.async_call("button", "press", {"entity_id": NEXT}, blocking=True)
    await hass.services.async_call("wereci", "toggle_ingredient", {"index": 1}, blocking=True)
    await _settle(hass)
    assert hass.states.get(SENSOR).attributes["step"] == 2
    assert relay.pushed[-1]["allIngredients"][1]["checked"] is True
    assert relay.pushed[-1]["labels"]["ready"] == "1 of 2 ready"
    assert hass.states.get(SENSOR).attributes["ingredients"][1]["checked"] is True


async def test_without_phone_needs_a_recipe_with_steps(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    player = _cast_player(hass)
    with pytest.raises(ServiceValidationError):
        await _show(hass, entity_id=player, without_phone=True)
    list_call.return_value = {"recipe_title": "A letter", "instructions": []}
    with pytest.raises(ServiceValidationError):
        await _show(hass, entity_id=player, recipe_id="r-9", without_phone=True)
    assert hass.states.get(SENSOR).state == "idle"


def test_sender_clamps_steps_and_ignores_what_it_cannot_do() -> None:
    from custom_components.wereci.sender import HaSender

    s = HaSender(None, "https://wereci.xyz", RECIPE, lambda: None)
    assert s.apply({"do": "step", "delta": -1}) is False
    assert s.apply({"do": "step", "delta": 9}) is True
    assert s.snapshot()["labels"]["stepOf"] == "Step 3 of 3"
    assert s.apply({"do": "scale", "factor": 2}) is False
    assert s.apply({"do": "toggle", "i": 7}) is False


async def test_a_name_that_matches_nothing_is_refused_not_guessed(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    """Semantic search always answers; a kitchen screen should not cook a guess."""
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    player = _cast_player(hass)
    list_call.return_value = {"hits": [{"id": "r-5", "title": "Baked ziti"}]}

    with pytest.raises(ServiceValidationError, match="Baked ziti"):
        await _show(hass, entity_id=player, recipe="lasagna", notify=[])
    assert hass.states.get(SENSOR).state == "idle"

    res = await _show(
        hass, entity_id=player, recipe="lasagna", allow_closest=True, notify=[]
    )
    assert res["recipe_id"] == "r-5"


def test_resembles() -> None:
    from custom_components.wereci.cook_display import resembles

    assert resembles("that pumpkin soup", "Pumpkin Curry Soup Recipe")
    assert resembles("carrot soup", "Carrots & ginger soup")
    assert not resembles("lasagna", "Baked ziti")
    assert not resembles("the recipe", "Baked ziti")  # nothing but filler
    # Half the words is the bar: "cake" alone carries a two-word ask.
    assert resembles("chocolate cake", "Carrot cake with pecans")
    assert not resembles("chocolate fudge brownies", "Carrot cake with pecans")


async def _type(hass: HomeAssistant, words: str) -> None:
    await hass.services.async_call(
        "text", "set_value", {"entity_id": RECIPE_BOX, "value": words}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)


async def _press_start(hass: HomeAssistant) -> None:
    await hass.services.async_call("button", "press", {"entity_id": START}, blocking=True)


PASTAS = {
    "hits": [
        {"id": "w-1", "title": "Pasta", "reference": True},
        {"id": "p-1", "title": "Baked Skillet Pasta", "cookbook": "NYT"},
        {"id": "p-2", "title": "Turmeric-Butter Pasta"},
        {"id": "p-3", "title": "Classic Italian Meatballs", "cuisine": "Italian"},
    ]
}


async def test_the_panel_starts_a_cook(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    shown = async_mock_service(hass, "cast", "show_lovelace_view")
    player = _cast_player(hass)
    hass.states.async_set(player, "off", {"friendly_name": "Kitchen display"})
    er.async_get(hass).async_update_entity(player, name="Kitchen display")
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="screen"):
        await _press_start(hass)

    assert hass.states.get(SCREEN).attributes["options"] == ["Kitchen display"]
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": SCREEN, "option": "Kitchen display"}, blocking=True,
    )
    assert hass.states.get(NO_PHONE).state == "on"  # the panel's default

    # Words that name one recipe: it is found and picked, Start cooks it.
    list_call.side_effect = lambda tool, args: (
        {"hits": [{"id": "r-2", "title": "Carrot soup"}, {"id": "r-9", "title": "Leek tart"}]}
        if tool == "search_recipes"
        else RECIPE
    )
    await _type(hass, "carrot soup")
    matches = hass.states.get(MATCHES)
    assert matches.state == "Carrot soup"
    assert matches.attributes["options"] == ["Carrot soup", "Leek tart"]
    assert matches.attributes["recipe_id"] == "r-2"

    await _press_start(hass)
    await _settle(hass)

    list_call.assert_any_await("get_recipe", {"id": "r-2"})
    assert shown[0].data["entity_id"] == player
    state = hass.states.get(SENSOR)
    assert (state.state, state.attributes["title"]) == ("cooking", "Carrot soup")
    assert state.attributes["driven_by"] == "home_assistant"


async def test_a_vague_word_offers_matches_and_waits_for_a_pick(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    player = _cast_player(hass)
    hass.states.async_set(player, "off", {"friendly_name": "Kitchen display"})
    er.async_get(hass).async_update_entity(player, name="Kitchen display")
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": SCREEN, "option": "Kitchen display"}, blocking=True,
    )

    list_call.side_effect = lambda tool, args: PASTAS if tool == "search_recipes" else RECIPE
    await _type(hass, "pasta")

    matches = hass.states.get(MATCHES)
    # Two titles say "pasta": neither is guessed. The encyclopedia card is not offered.
    assert matches.state == "unknown"
    assert matches.attributes["options"] == [
        "Baked Skillet Pasta", "Turmeric-Butter Pasta", "Classic Italian Meatballs"
    ]
    assert matches.attributes["searching"] is False
    with pytest.raises(ServiceValidationError, match="pick one"):
        await _press_start(hass)

    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": MATCHES, "option": "Classic Italian Meatballs"}, blocking=True,
    )
    assert hass.states.get(MATCHES).attributes["recipe_id"] == "p-3"
    await _press_start(hass)
    await _settle(hass)
    list_call.assert_any_await("get_recipe", {"id": "p-3"})
    assert hass.states.get(SENSOR).state == "cooking"


async def test_search_failures_and_empty_results_say_so(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    player = _cast_player(hass)
    hass.states.async_set(player, "off", {"friendly_name": "Kitchen display"})
    er.async_get(hass).async_update_entity(player, name="Kitchen display")
    await hass.async_block_till_done()
    await hass.services.async_call(
        "select", "select_option",
        {"entity_id": SCREEN, "option": "Kitchen display"}, blocking=True,
    )

    list_call.side_effect = lambda tool, args: {"hits": []}
    await _type(hass, "zzz")
    with pytest.raises(ServiceValidationError, match="found nothing"):
        await _press_start(hass)

    # Pressed with words nobody searched for yet (a restart): Start searches.
    entry.runtime_data.cook_display.panel.searched = None
    with pytest.raises(ServiceValidationError, match="found nothing"):
        await _press_start(hass)
    assert entry.runtime_data.cook_display.panel.searched == "zzz"

    # weReci unreachable: the reason reaches the Matches entity and the button.
    list_call.side_effect = WereciError("ReadTimeout")
    await _type(hass, "soup")
    assert hass.states.get(MATCHES).attributes["error"] == "weReci: ReadTimeout"
    with pytest.raises(HomeAssistantError, match="ReadTimeout"):
        await _press_start(hass)


def _tools(answers: dict):
    """list_call side effect: one canned answer (or a list of them) per tool."""
    calls: list[tuple[str, dict]] = []

    def answer(tool: str, args: dict):
        calls.append((tool, args))
        got = answers[tool]
        return got.pop(0) if isinstance(got, list) else got

    return answer, calls


async def _cook(hass, entry, list_call, answers) -> list:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    answer, calls = _tools({"get_recipe": RECIPE, **answers})
    list_call.side_effect = answer
    await _show(hass, entity_id=_cast_player(hass), recipe_id="r-2", without_phone=True)
    await _settle(hass)
    return calls


async def _tap(hass: HomeAssistant, relay, cmd: dict) -> None:
    """A tap on the screen: posted with the display's token, like the Hub does."""
    await relay.post("https://x/command", json={"token": TOKEN, "cmd": cmd})
    await _settle(hass)


async def test_scaling_from_the_screen_is_weRecis_own_run(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    scaled = {"scaledIngredients": ["2 onions", "1 kg carrots"]}
    with patch("custom_components.wereci.sender._SCALE_POLL_SECONDS", 0):
        calls = await _cook(hass, entry, list_call, {
            "scale_recipe": {"status": "pending", "job_id": "j1"},
            "get_scale_job": [{"state": "running"}, {"state": "done", "result": scaled}],
        })
        await _tap(hass, relay, {"do": "scale", "factor": 2})
        await _settle(hass)

    assert ("scale_recipe", {"recipe_id": "r-2", "factor": 2.0, "substitutions": {}}) in calls
    assert [c for c in calls if c[0] == "get_scale_job"] == [("get_scale_job", {"job_id": "j1"})] * 2
    assert any(p["busyLabel"] == "Scaling…" for p in relay.pushed)  # the screen says so
    last = relay.pushed[-1]
    assert (last["scaleLabel"], last["busyLabel"]) == ("2×", None)
    assert [i["text"] for i in last["allIngredients"]] == ["2 onions", "1 kg carrots"]

    await _tap(hass, relay, {"do": "scale", "factor": 1})
    assert relay.pushed[-1]["allIngredients"][0]["text"] == "1 onion"


async def test_a_swap_is_offered_picked_and_restored(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _cook(hass, entry, list_call, {
        "suggest_substitute": {"suggestions": [
            {"replacement": "2 shallots", "ratio": "2:1", "confidence": "high",
             "reasoning": "Milder allium."},
        ]},
    })
    await _tap(hass, relay, {"do": "swapOpts", "i": 0})
    panel = relay.pushed[-1]["swapPanel"]
    assert (panel["status"], panel["options"][0]["replacement"]) == ("ready", "2 shallots")
    assert panel["options"][0]["ratioText"] == "Ratio: 2:1"
    assert "_found" not in panel

    await _tap(hass, relay, {"do": "swapPick", "i": 0, "k": 0})
    assert relay.pushed[-1]["allIngredients"][0]["text"] == "2 shallots"
    assert relay.pushed[-1]["swapPanel"] is None

    await _tap(hass, relay, {"do": "swapClear", "i": 0})
    assert relay.pushed[-1]["allIngredients"][0]["text"] == "1 onion"


async def test_a_connection_without_cook_assist_is_asked_to_sign_in_again(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _cook(hass, entry, list_call, {"scale_recipe": {"error": "permission_required"}})
    await _tap(hass, relay, {"do": "scale", "factor": 2})

    last = relay.pushed[-1]
    assert last["controls"]["scale"] is False and last["scaleLabel"] is None
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == ["reauth"]


async def test_a_breakdown_made_in_the_app_can_be_toggled_never_made(
    hass: HomeAssistant, entry, list_call, relay
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    finer = ["Peel onions.", "Slice onions.", "Sweat them.", "Add carrots.", "Simmer.", "Blend."]
    answer, calls = _tools({"get_recipe": {**RECIPE, "reformulated_instructions": finer}})
    list_call.side_effect = answer
    await _show(hass, entity_id=_cast_player(hass), recipe_id="r-2", without_phone=True)
    await _settle(hass)
    assert relay.pushed[0]["controls"]["breakdown"] is True

    await _tap(hass, relay, {"do": "step", "delta": 2})  # last of 3 written steps
    await _tap(hass, relay, {"do": "breakdown"})
    last = relay.pushed[-1]
    assert (last["breakdownActive"], last["totalSteps"], last["stepIdx"]) == (True, 6, 5)
    assert {c[0] for c in calls} == {"get_recipe"}  # nothing was generated
