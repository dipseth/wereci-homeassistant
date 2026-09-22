"""weReci's MCP tools, offered to Assist conversation agents as an LLM API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging

import httpx
from mcp import McpError
from probatio import from_openapi
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.json import JsonObjectType

from .coordinator import tag_along_list_refresh
from .api import TokenManager, mcp_session
from .const import DOMAIN, TOOL_TIMEOUT, TOOLS_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)

API_PROMPT = (
    "These tools reach the user's weReci cookbook: search and read their "
    "recipes, browse their curated shelves, and manage their shopping list. "
    "Recipe ids must be copied exactly from tool results."
)


class WereciTool(llm.Tool):
    """One weReci MCP tool."""

    def __init__(
        self,
        name: str,
        description: str | None,
        parameters: vol.Schema,
        url: str,
        entry: ConfigEntry,
        token_manager: TokenManager,
    ) -> None:
        """Initialize."""
        self.name = name
        self.description = description
        self.parameters = parameters
        self._url = url
        self._entry = entry
        self._token_manager = token_manager

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> JsonObjectType:
        """Call the tool over a fresh MCP session."""
        try:
            async with asyncio.timeout(TOOL_TIMEOUT):
                async with mcp_session(hass, self._url, self._token_manager) as s:
                    result = await s.call_tool(tool_input.tool_name, tool_input.tool_args)
        except TimeoutError as err:
            raise HomeAssistantError("weReci took too long to answer") from err
        except OAuth2TokenRequestReauthError as err:
            self._entry.async_start_reauth(hass)
            raise HomeAssistantError("weReci sign-in expired") from err
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                self._entry.async_start_reauth(hass)
            raise HomeAssistantError(f"weReci error: {err}") from err
        except (httpx.HTTPError, McpError) as err:
            raise HomeAssistantError(f"weReci error: {err}") from err
        tag_along_list_refresh(hass, self._entry)
        return result.model_dump(exclude_unset=True, exclude_none=True)


class WereciToolsCoordinator(DataUpdateCoordinator[list[llm.Tool]]):
    """Keeps the tool list fresh — the surface grows server-side."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        url: str,
        token_manager: TokenManager,
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} tools",
            update_interval=TOOLS_POLL_INTERVAL,
        )
        self._url = url
        self._token_manager = token_manager

    async def _async_update_data(self) -> list[llm.Tool]:
        try:
            async with asyncio.timeout(TOOL_TIMEOUT):
                async with mcp_session(self.hass, self._url, self._token_manager) as s:
                    listed = await s.list_tools()
        except OAuth2TokenRequestReauthError as err:
            raise ConfigEntryAuthFailed("weReci sign-in expired") from err
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                raise ConfigEntryAuthFailed("weReci rejected the token") from err
            raise UpdateFailed(str(err)) from err
        except (TimeoutError, httpx.HTTPError, McpError) as err:
            raise UpdateFailed(str(err) or type(err).__name__) from err

        tools: list[llm.Tool] = []
        for tool in listed.tools:
            try:
                parameters = from_openapi(tool.inputSchema)
            except Exception as err:  # noqa: BLE001 - one bad schema must not drop the rest
                _LOGGER.warning("Skipping weReci tool %s: %s", tool.name, err)
                continue
            tools.append(
                WereciTool(
                    tool.name,
                    tool.description,
                    parameters,
                    self._url,
                    self.config_entry,
                    self._token_manager,
                )
            )
        return tools


@dataclass(kw_only=True)
class WereciAPI(llm.API):
    """The LLM API a conversation agent can be given."""

    coordinator: WereciToolsCoordinator

    async def async_get_api_instance(
        self, llm_context: llm.LLMContext
    ) -> llm.APIInstance:
        """Return the API instance."""
        return llm.APIInstance(
            self, API_PROMPT, llm_context, tools=self.coordinator.data
        )
