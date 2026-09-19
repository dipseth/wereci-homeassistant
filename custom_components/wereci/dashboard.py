"""The dashboard the cook display is shown through — provided, not hand-built.

Cast devices and browser_mod browsers are shown a Lovelace view, so there has
to be one. The integration registers it: one panel view per account, holding a
single webpage card on that account's `display_path`. It is generated on every
load (nothing is stored), read-only, and kept out of the sidebar.
"""

from __future__ import annotations

import logging
from typing import Any, override

from homeassistant.components import frontend
from homeassistant.components.lovelace import dashboard
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.core import HomeAssistant, callback
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
        return {
            "title": "weReci",
            "views": [
                {
                    "path": entry.runtime_data.cook_display.view_path,
                    "title": f"Cook display ({entry.title})",
                    "panel": True,
                    "cards": [
                        {
                            "type": "iframe",
                            "url": base + entry.runtime_data.cook_display.display_path,
                            "aspect_ratio": "56%",
                        }
                    ],
                }
                for entry in self.hass.config_entries.async_loaded_entries(DOMAIN)
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
        sidebar_title="weReci cook display",
        sidebar_icon="mdi:chef-hat",
        require_admin=False,
        show_in_sidebar=False,
        config={"mode": MODE_YAML},
    )
    return cook
