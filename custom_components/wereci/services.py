"""Services and voice intents for the cook display."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, intent

from .const import (
    DEFAULT_DASHBOARD_PATH,
    DOMAIN,
    INTENT_NEXT_STEP,
    INTENT_PREVIOUS_STEP,
    SERVICE_SHOW_COOK_DISPLAY,
    SERVICE_STOP_COOK_DISPLAY,
    SERVICE_TOGGLE_INGREDIENT,
)
from .cook_display import (
    STATUS_COOKING,
    CookDisplay,
    resolve_recipe,
    resolve_sender,
    resolve_target,
)

_ENTRY = {vol.Optional("config_entry_id"): cv.string}

SHOW_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("browser_id"): cv.string,
        vol.Optional("recipe_id"): cv.string,
        vol.Optional("recipe"): cv.string,
        vol.Optional("without_phone", default=False): cv.boolean,
        vol.Optional("allow_closest", default=False): cv.boolean,
        vol.Optional("notify"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("dashboard_path", default=DEFAULT_DASHBOARD_PATH): cv.string,
        vol.Optional("view_path"): cv.string,
        vol.Optional("return_path", default="/"): cv.string,
        **_ENTRY,
    }
)
STOP_SCHEMA = vol.Schema(_ENTRY)


def _displays(hass: HomeAssistant) -> list[CookDisplay]:
    return [
        e.runtime_data.cook_display
        for e in hass.config_entries.async_loaded_entries(DOMAIN)
    ]


def _runtime(hass: HomeAssistant, call: ServiceCall) -> Any:
    wanted = call.data.get("config_entry_id")
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if wanted in (None, entry.entry_id):
            return entry.runtime_data
    raise ServiceValidationError("No weReci account is set up")


def _display(hass: HomeAssistant, call: ServiceCall) -> CookDisplay:
    return _runtime(hass, call).cook_display


async def async_start_cook(
    hass: HomeAssistant, runtime: Any, data: dict[str, Any]
) -> dict[str, str]:
    """What show_cook_display does — also the control panel's Start button."""
    display: CookDisplay = runtime.cook_display
    target = resolve_target(
        hass, data.get("entity_id"), data.get("browser_id"), data.get("return_path", "/")
    )
    call_tool = runtime.list_coordinator.async_call_tool
    recipe = await resolve_recipe(
        call_tool,
        data.get("recipe_id"),
        data.get("recipe"),
        bool(data.get("allow_closest")),
    )
    sender = (
        await resolve_sender(
            hass,
            call_tool,
            display.base_url,
            recipe,
            # Connected before cook:assist existed: the sign-in flow is how a
            # connection gains a permission, so ask for it again.
            lambda: runtime.entry.async_start_reauth(hass),
            runtime.list_coordinator.async_request,
        )
        if data.get("without_phone")
        else None
    )
    return await display.async_show(
        target,
        recipe=recipe,
        sender=sender,
        dashboard_path=data.get("dashboard_path") or DEFAULT_DASHBOARD_PATH,
        view_path=data.get("view_path") or display.view_path,
        notify=data.get("notify"),
    )


async def async_start_from_panel(hass: HomeAssistant, runtime: Any) -> None:
    """The Start button: the panel's three choices → one cook."""
    panel = runtime.cook_display.panel
    if not panel.screen:
        raise ServiceValidationError("Pick a screen first")
    if panel.without_phone and not panel.recipe:
        raise ServiceValidationError("Type a recipe first — or turn off Without a phone")
    display = runtime.cook_display
    if panel.recipe and not panel.match_id and panel.searched != panel.recipe:
        # Pressed mid-search, or the words outlived a restart: search first.
        if not panel.searching:
            display.async_search(runtime.list_coordinator.async_call_tool, panel.recipe)
        await display.async_search_settled()
    if panel.recipe and not panel.match_id:
        if panel.search_error:
            raise HomeAssistantError(panel.search_error)
        if not panel.matches:
            raise ServiceValidationError(f"weReci found nothing for “{panel.recipe}”")
        raise ServiceValidationError(
            f"“{panel.recipe}” fits more than one recipe — pick one under Matches"
        )
    await async_start_cook(
        hass,
        runtime,
        {
            "entity_id": panel.screen,
            "without_phone": panel.without_phone,
            **({"recipe_id": panel.match_id} if panel.match_id else {}),
        },
    )


class StepIntent(intent.IntentHandler):
    """“Next step” / “previous step”, with no LLM in the loop."""

    description = "Move the weReci cook display one step"

    def __init__(self, intent_type: str, delta: int) -> None:
        """Initialize."""
        self.intent_type = intent_type
        self._delta = delta

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        """Step whichever display is cooking."""
        cooking = [
            d for d in _displays(intent_obj.hass) if d.status == STATUS_COOKING
        ]
        if not cooking:
            raise intent.IntentHandleError("Nothing is cooking on a weReci display")
        await cooking[0].async_command({"do": "step", "delta": self._delta})
        return intent_obj.create_response()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the two services and the two intents."""

    async def show(call: ServiceCall) -> ServiceResponse:
        return await async_start_cook(hass, _runtime(hass, call), dict(call.data))

    async def toggle(call: ServiceCall) -> None:
        await _display(hass, call).async_command(
            {"do": "toggle", "i": call.data["index"]}
        )

    async def stop(call: ServiceCall) -> None:
        await _display(hass, call).async_stop()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SHOW_COOK_DISPLAY,
        show,
        schema=SHOW_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_COOK_DISPLAY, stop, schema=STOP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TOGGLE_INGREDIENT,
        toggle,
        schema=vol.Schema({vol.Required("index"): cv.positive_int, **_ENTRY}),
    )
    intent.async_register(hass, StepIntent(INTENT_NEXT_STEP, 1))
    intent.async_register(hass, StepIntent(INTENT_PREVIOUS_STEP, -1))
