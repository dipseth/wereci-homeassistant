"""weReci: recipe tools for Assist, and the synced shopping list as a to-do list."""

from __future__ import annotations

from typing import cast

from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow, llm

from .api import register_implementation
from .const import (
    CONF_AUTHORIZE_URL,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_TOKEN_URL,
    DOMAIN,
    MCP_PATH,
)
from .coordinator import WereciConfigEntry, WereciListCoordinator, WereciRuntime
from .llm_api import WereciAPI, WereciToolsCoordinator

PLATFORMS = [Platform.TODO]


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
    entry.runtime_data = WereciRuntime(list_coordinator, tools_coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WereciConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
