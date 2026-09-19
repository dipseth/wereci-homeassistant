"""The weReci dashboard — provided, not hand-built.

Two views per account, generated on every load (nothing is stored, read-only):

- a control panel: what is cooking, step buttons, the ingredient list, stop,
  and a live copy of the screen that takes taps like the screen itself;
- the display view Cast devices and browser_mod browsers are shown: a single
  full-screen webpage card on `display_path`. Hidden from the tabs; reached
  by path.
"""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.components import frontend
from homeassistant.components.lovelace import dashboard
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes, json_fragment
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DEFAULT_DASHBOARD_PATH, DOMAIN

_LOGGER = logging.getLogger(__name__)


class CookDashboard(dashboard.LovelaceConfig):
    """A generated dashboard: one cook-display view per loaded account."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        super().__init__(hass, DEFAULT_DASHBOARD_PATH, {"mode": MODE_YAML})

    @property
    @override
    def mode(self) -> str:
        """YAML mode is what makes the frontend treat it as read-only."""
        return MODE_YAML

    def _build(self) -> dict[str, Any]:
        # HA's Cast receiver is served from another origin, so a relative card
        # URL would resolve there. Absolute, and external when there is one.
        try:
            base = get_url(self.hass, prefer_external=True)
        except NoURLAvailableError:
            base = ""
        views: list[dict[str, Any]] = []
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            display = entry.runtime_data.cook_display
            url = base + display.display_path
            views.append(self._control_view(entry, url))
            views.append(
                {
                    "path": display.view_path,
                    "title": f"Cook display ({entry.title})",
                    "visible": False,
                    "panel": True,
                    "cards": [{"type": "iframe", "url": url, "aspect_ratio": "56%"}],
                }
            )
        return {"title": "weReci", "views": views}

    def _control_view(self, entry: Any, display_url: str) -> dict[str, Any]:
        reg = er.async_get(self.hass)

        def entity(platform: str, key: str) -> str | None:
            return reg.async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_{key}")

        sensor = entity("sensor", "cooking")
        def press(key: str) -> dict[str, Any]:
            return {
                "action": "perform-action",
                "perform_action": "button.press",
                "target": {"entity_id": entity("button", key)},
            }

        now = (
            f"{{% set s = '{sensor}' %}}"
            "{% if is_state(s, 'cooking') %}"
            "## {{ state_attr(s, 'title') }}\n"
            "**Step {{ state_attr(s, 'step') }} of {{ state_attr(s, 'total_steps') }}**"
            "\n\n{{ state_attr(s, 'step_text') }}"
            "{% elif is_state(s, 'waiting') %}"
            "## Waiting for a phone\nOpen Cook Mode and enter "
            "**{{ state_attr(s, 'code') }}**."
            "{% else %}"
            "## Nothing is cooking\nStart with the **weReci: Show cook display** "
            "action — add a recipe and *Without a phone* to cook from here."
            "{% endif %}"
        )
        ingredients = (
            f"{{% set s = '{sensor}' %}}"
            "{% for i in state_attr(s, 'ingredients') or [] %}"
            "{{ '✅' if i.checked else '⬜' }} {{ i.text }}\n"
            "{% endfor %}"
        )
        return {
            "path": f"control-{entry.entry_id.lower()}",
            "title": entry.title,
            "icon": "mdi:chef-hat",
            "cards": [
                {"type": "markdown", "content": now},
                {
                    "type": "horizontal-stack",
                    "cards": [
                        {
                            "type": "button",
                            "name": "Previous step",
                            "icon": "mdi:chevron-left",
                            "tap_action": press("previous_step"),
                        },
                        {
                            "type": "button",
                            "name": "Next step",
                            "icon": "mdi:chevron-right",
                            "tap_action": press("next_step"),
                        },
                        {
                            "type": "button",
                            "name": "Stop",
                            "icon": "mdi:stop-circle-outline",
                            "tap_action": {
                                "action": "perform-action",
                                "perform_action": f"{DOMAIN}.stop_cook_display",
                                "data": {"config_entry_id": entry.entry_id},
                            },
                        },
                    ],
                },
                # The receiver itself: reads are idempotent, so a second copy
                # of the screen is free — and it takes taps like the screen.
                {"type": "iframe", "url": display_url, "aspect_ratio": "56%"},
                {"type": "markdown", "title": "Ingredients", "content": ingredients},
            ],
        }

    @override
    async def async_get_info(self) -> dict[str, Any]:
        """Describe the dashboard."""
        return {"mode": self.mode, "views": len(self._build()["views"])}

    @override
    async def async_load(self, force: bool) -> dict[str, Any]:
        """Build the config."""
        return self._build()

    @override
    async def async_json(self, force: bool) -> json_fragment:
        """Build the config, as JSON."""
        return json_fragment(json_bytes(self._build()))

    @callback
    def async_accounts_changed(self) -> None:
        """An account loaded or unloaded: open frontends should re-read."""
        self._config_updated()


@callback
def async_setup_dashboard(hass: HomeAssistant) -> CookDashboard | None:
    """Register the dashboard, unless the user already owns that URL."""
    dashboards = hass.data[LOVELACE_DATA].dashboards
    if DEFAULT_DASHBOARD_PATH in dashboards:
        _LOGGER.info(
            "A dashboard at /%s already exists; weReci will use it as it is",
            DEFAULT_DASHBOARD_PATH,
        )
        return None
    cook = CookDashboard(hass)
    dashboards[DEFAULT_DASHBOARD_PATH] = cook
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        frontend_url_path=DEFAULT_DASHBOARD_PATH,
        sidebar_title="weReci",
        sidebar_icon="mdi:chef-hat",
        require_admin=False,
        show_in_sidebar=True,
        config={"mode": MODE_YAML},
    )
    return cook
