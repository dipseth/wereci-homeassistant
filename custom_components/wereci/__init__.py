"""weReci: recipe tools for Assist, and the synced shopping list as a to-do list."""

from __future__ import annotations

from typing import cast

from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_entry_oauth2_flow,
    config_validation as cv,
    llm,
)
from homeassistant.helpers.typing import ConfigType

from .api import register_implementation
from .const import (
    CONF_AUTHORIZE_URL,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_TOKEN_URL,
    DOMAIN,
    MCP_PATH,
)
from .cook_display import CookDisplay, CookDisplayView, ensure_display_secret
from .coordinator import WereciConfigEntry, WereciListCoordinator, WereciRuntime
from .dashboard import async_setup_dashboard
from .llm_api import WereciAPI, WereciToolsCoordinator
from .services import async_setup_services

PLATFORMS = [
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register what is not tied to one account: services, intents, the view."""
    async_setup_services(hass)
    hass.http.register_view(CookDisplayView())
    hass.data[DOMAIN] = async_setup_dashboard(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WereciConfigEntry) -> bool:
    """Set up weReci from a config entry."""
    # The OAuth client was registered dynamically at config time and lives in
    # the entry, so the implementation has to be rebuilt from it on every start.
    register_implementation(
        hass,
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_AUTHORIZE_URL],
        entry.data[CONF_TOKEN_URL],
    )
    try:
        implementation = (
            await config_entry_oauth2_flow.async_get_config_entry_implementation(
                hass, entry
            )
        )
    except config_entry_oauth2_flow.ImplementationUnavailableError as err:
        raise ConfigEntryNotReady("weReci OAuth is not available") from err
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def token_manager() -> str:
        await session.async_ensure_token_valid()
        return cast(str, session.token[CONF_ACCESS_TOKEN])

    url = f"{entry.data[CONF_BASE_URL]}{MCP_PATH}"
    list_coordinator = WereciListCoordinator(hass, entry, url, token_manager)
    tools_coordinator = WereciToolsCoordinator(hass, entry, url, token_manager)
    await tools_coordinator.async_config_entry_first_refresh()
    await list_coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(
        llm.async_register_api(
            hass,
            WereciAPI(
                hass=hass,
                id=f"{DOMAIN}-{entry.entry_id}",
                name=f"weReci ({entry.title})",
                coordinator=tools_coordinator,
            ),
        )
    )
    ensure_display_secret(hass, entry)
    cook_display = CookDisplay(hass, entry, entry.data[CONF_BASE_URL])
    entry.runtime_data = WereciRuntime(list_coordinator, tools_coordinator, cook_display)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _dashboard_changed(hass)
    entry.async_on_unload(lambda: _dashboard_changed(hass))
    return True


def _dashboard_changed(hass: HomeAssistant) -> None:
    if (cook_dashboard := hass.data.get(DOMAIN)) is not None:
        cook_dashboard.async_accounts_changed()


async def async_unload_entry(hass: HomeAssistant, entry: WereciConfigEntry) -> bool:
    """Unload a config entry."""
    # The channel is closed, but the screen is left alone: a restart or reload
    # should not blank the kitchen display mid-recipe.
    await entry.runtime_data.cook_display.async_stop(restore=False)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
