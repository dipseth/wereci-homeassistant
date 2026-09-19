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
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, intent

from .const import (
    DEFAULT_DASHBOARD_PATH,
    DEFAULT_VIEW_PATH,
    DOMAIN,
    INTENT_NEXT_STEP,
    INTENT_PREVIOUS_STEP,
    SERVICE_SHOW_COOK_DISPLAY,
    SERVICE_STOP_COOK_DISPLAY,
)
from .cook_display import STATUS_COOKING, CookDisplay, resolve_target

_ENTRY = {vol.Optional("config_entry_id"): cv.string}

SHOW_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_id"): cv.entity_id,
        vol.Optional("browser_id"): cv.string,
        vol.Optional("notify"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("dashboard_path", default=DEFAULT_DASHBOARD_PATH): cv.string,
        vol.Optional("view_path", default=DEFAULT_VIEW_PATH): cv.string,
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


def _display(hass: HomeAssistant, call: ServiceCall) -> CookDisplay:
    wanted = call.data.get("config_entry_id")
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if wanted in (None, entry.entry_id):
            return entry.runtime_data.cook_display
    raise ServiceValidationError("No weReci account is set up")


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
        display = _display(hass, call)
        data: dict[str, Any] = dict(call.data)
        target = resolve_target(
            hass, data.get("entity_id"), data.get("browser_id"), data["return_path"]
        )
        return await display.async_show(
            target,
            dashboard_path=data["dashboard_path"],
            view_path=data["view_path"],
            notify=data.get("notify"),
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
    intent.async_register(hass, StepIntent(INTENT_NEXT_STEP, 1))
    intent.async_register(hass, StepIntent(INTENT_PREVIOUS_STEP, -1))
