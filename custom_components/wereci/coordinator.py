"""Coordinators: the synced shopping list, and the tool list for Assist."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
from typing import Any

import httpx
from mcp import McpError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TokenManager, WereciError, mcp_session, tool_json
from .const import (
    DOMAIN,
    LIST_POLL_INTERVAL,
    LIST_TIMEOUT,
    TOOL_GET_LIST,
    TOOL_UPDATE_LIST,
)

_LOGGER = logging.getLogger(__name__)

type WereciConfigEntry = ConfigEntry[WereciRuntime]


@dataclass
class ListItem:
    """One line of the synced list."""

    key: str
    text: str
    checked: bool
    aisle: str | None
    recipes: list[str]


@dataclass
class ListState:
    """The synced list as weReci last described it."""

    available: bool
    reason: str | None = None
    seq: int | None = None
    items: list[ListItem] = field(default_factory=list)


@dataclass
class WereciRuntime:
    """What a loaded config entry carries."""

    list_coordinator: WereciListCoordinator
    tools_coordinator: Any


def parse_list(data: dict[str, Any]) -> ListState:
    """A get/update_shopping_list answer → ListState."""
    if data.get("error") == "permission_required":
        return ListState(available=False, reason="permission_required")
    if not data.get("available"):
        return ListState(available=False, reason=str(data.get("reason", "unavailable")))
    return ListState(
        available=True,
        seq=data.get("seq"),
        items=[
            ListItem(
                key=str(raw["key"]),
                text=str(raw.get("text") or raw["key"]),
                checked=bool(raw.get("checked")),
                aisle=raw.get("aisle"),
                recipes=[str(r) for r in raw.get("recipes") or []],
            )
            for raw in data.get("items") or []
            if isinstance(raw, dict) and raw.get("key")
        ],
    )


class WereciListCoordinator(DataUpdateCoordinator[ListState]):
    """Polls the list's seq; re-reads the list only when it moved."""

    config_entry: WereciConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: WereciConfigEntry,
        url: str,
        token_manager: TokenManager,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} list",
            update_interval=LIST_POLL_INTERVAL,
        )
        self._url = url
        self._token_manager = token_manager

    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            async with asyncio.timeout(LIST_TIMEOUT):
                async with mcp_session(self.hass, self._url, self._token_manager) as s:
                    return tool_json(await s.call_tool(tool, args))
        except OAuth2TokenRequestReauthError as err:
            raise ConfigEntryAuthFailed("weReci sign-in expired") from err
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                raise ConfigEntryAuthFailed("weReci rejected the token") from err
            raise WereciError(str(err)) from err
        except (TimeoutError, httpx.HTTPError, McpError) as err:
            raise WereciError(str(err) or type(err).__name__) from err

    async def _async_update_data(self) -> ListState:
        prev = self.data
        args: dict[str, Any] = {}
        if prev is not None and prev.available and prev.seq is not None:
            args["since"] = prev.seq
        try:
            data = await self._call(TOOL_GET_LIST, args)
        except WereciError as err:
            raise UpdateFailed(str(err)) from err
        if data.get("unchanged") and prev is not None:
            return prev
        return parse_list(data)

    async def async_change(self, **changes: list[str]) -> None:
        """Apply check / uncheck / remove / add, and take the answer as state."""
        args = {k: v for k, v in changes.items() if v}
        if not args:
            return
        try:
            data = await self._call(TOOL_UPDATE_LIST, args)
        except WereciError as err:
            raise HomeAssistantError(f"weReci: {err}") from err
        state = parse_list(data)
        if not state.available:
            raise HomeAssistantError(f"weReci list is not available: {state.reason}")
        self.async_set_updated_data(state)
