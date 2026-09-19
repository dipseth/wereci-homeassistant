"""Talking to weReci: OAuth (PKCE + dynamic registration) and the MCP session."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
import json
import logging
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.httpx_client import create_async_httpx_client, get_async_client

from .const import (
    CLIENT_NAME,
    DOMAIN,
    MY_REDIRECT_URI,
    OAUTH_DISCOVERY_PATH,
    SCOPES,
    TOOL_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

type TokenManager = Callable[[], Awaitable[str]]


class WereciError(Exception):
    """weReci could not be reached, or answered with something unusable."""


class WereciRegistrationError(WereciError):
    """Dynamic client registration was refused."""


class WereciOAuth2Implementation(
    config_entry_oauth2_flow.LocalOAuth2ImplementationWithPkce
):
    """A public PKCE client — weReci issues no client secrets."""

    @property
    def name(self) -> str:
        """Name shown if the user is ever asked to pick an implementation."""
        return "weReci"

    @property
    def extra_authorize_data(self) -> dict:
        """Ask for the scopes this integration uses, on top of the PKCE pair."""
        return {"scope": SCOPES, **super().extra_authorize_data}


def register_implementation(
    hass: HomeAssistant, client_id: str, authorize_url: str, token_url: str
) -> None:
    """(Re-)register the OAuth implementation for this Home Assistant."""
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        WereciOAuth2Implementation(
            hass, DOMAIN, client_id, authorize_url, token_url
        ),
    )


async def async_discover_oauth(hass: HomeAssistant, base_url: str) -> dict[str, str]:
    """Read the authorization server metadata (RFC 8414)."""
    try:
        resp = await get_async_client(hass).get(
            f"{base_url}{OAUTH_DISCOVERY_PATH}", timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as err:
        raise WereciError(f"OAuth discovery failed: {err}") from err
    try:
        return {
            "authorize_url": data["authorization_endpoint"],
            "token_url": data["token_endpoint"],
            "registration_url": data["registration_endpoint"],
        }
    except KeyError as err:
        raise WereciError(f"OAuth metadata is missing {err}") from err


async def async_register_client(
    hass: HomeAssistant, registration_url: str, redirect_uri: str
) -> str:
    """Register this Home Assistant as a public OAuth client (RFC 7591)."""
    redirect_uris = list(dict.fromkeys([redirect_uri, MY_REDIRECT_URI]))
    try:
        resp = await get_async_client(hass).post(
            registration_url,
            json={
                "client_name": CLIENT_NAME,
                "redirect_uris": redirect_uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
            timeout=15,
        )
    except httpx.HTTPError as err:
        raise WereciError(f"Client registration failed: {err}") from err
    if resp.status_code >= 400:
        # weReci only accepts https (or localhost) redirects: a plain-http
        # Home Assistant without My Home Assistant lands here.
        raise WereciRegistrationError(resp.text[:300])
    try:
        return str(resp.json()["client_id"])
    except (ValueError, KeyError) as err:
        raise WereciError("Client registration returned no client_id") from err


def token_claims(access_token: str) -> dict[str, Any]:
    """Read (not verify) the access token's claims — `sub` is the account.

    Only used to name the config entry and keep one entry per account; the
    server is what verifies the token on every call.
    """
    try:
        payload = access_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


@asynccontextmanager
async def mcp_session(
    hass: HomeAssistant, url: str, token_manager: TokenManager
) -> AsyncGenerator[ClientSession]:
    """Open one Streamable HTTP MCP session, authorized as the account."""
    headers = {"Authorization": f"Bearer {await token_manager()}"}
    try:
        async with (
            streamable_http_client(
                url=url,
                # httpx's own default is 5s a read — a recipe search on a cold
                # weReci takes longer. The callers' asyncio.timeout is the limit.
                http_client=create_async_httpx_client(
                    hass, headers=headers, timeout=httpx.Timeout(TOOL_TIMEOUT, connect=10)
                ),
            ) as (read_stream, write_stream, _),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            yield session
    except ExceptionGroup as err:
        # anyio wraps transport failures; surface the first real one.
        raise err.exceptions[0] from err


def tool_json(result: CallToolResult) -> dict[str, Any]:
    """weReci tools answer with one JSON text block — parse it."""
    for block in result.content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise WereciError("Tool result carried no JSON object")
