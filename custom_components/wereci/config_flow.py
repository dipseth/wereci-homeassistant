"""Config flow: register this Home Assistant with weReci, then sign in."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.helpers import config_entry_oauth2_flow

from .api import (
    WereciError,
    WereciRegistrationError,
    async_discover_oauth,
    async_register_client,
    register_implementation,
    token_claims,
)
from .const import (
    CONF_AUTHORIZE_URL,
    CONF_BASE_URL,
    CONF_CLIENT_ID,
    CONF_TOKEN_URL,
    DEFAULT_BASE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class WereciFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Sign in to weReci with OAuth 2.1 (PKCE, no client secret)."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Set up the flow."""
        super().__init__()
        self._client: dict[str, str] = {}

    @property
    def logger(self) -> logging.Logger:
        """Return the logger."""
        return _LOGGER

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Make sure an OAuth client exists, then hand over to the OAuth flow."""
        if (error := await self._async_ensure_client()) is not None:
            return self.async_abort(reason=error)
        return await self.async_step_pick_implementation()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start over when weReci stops accepting the refresh token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then sign in again."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def _async_ensure_client(self) -> str | None:
        """Reuse this Home Assistant's registered client, or register one.

        One client per Home Assistant, shared by every weReci account added to
        it — HA keys OAuth implementations by domain, not by config entry.
        """
        for entry in self._async_current_entries(include_ignore=False):
            if CONF_CLIENT_ID in entry.data:
                self._client = {
                    key: entry.data[key]
                    for key in (
                        CONF_BASE_URL,
                        CONF_CLIENT_ID,
                        CONF_AUTHORIZE_URL,
                        CONF_TOKEN_URL,
                    )
                }
                break
        else:
            try:
                meta = await async_discover_oauth(self.hass, DEFAULT_BASE_URL)
                client_id = await async_register_client(
                    self.hass,
                    meta["registration_url"],
                    config_entry_oauth2_flow.async_get_redirect_uri(self.hass),
                )
            except WereciRegistrationError as err:
                _LOGGER.error("weReci refused the client registration: %s", err)
                return "registration_refused"
            except WereciError as err:
                _LOGGER.error("Could not reach weReci: %s", err)
                return "cannot_connect"
            self._client = {
                CONF_BASE_URL: DEFAULT_BASE_URL,
                CONF_CLIENT_ID: client_id,
                CONF_AUTHORIZE_URL: meta["authorize_url"],
                CONF_TOKEN_URL: meta["token_url"],
            }
        register_implementation(
            self.hass,
            self._client[CONF_CLIENT_ID],
            self._client[CONF_AUTHORIZE_URL],
            self._client[CONF_TOKEN_URL],
        )
        return None

    async def async_oauth_create_entry(self, data: dict) -> ConfigFlowResult:
        """Name the entry after the account and keep one entry per account."""
        claims = token_claims(data[CONF_TOKEN][CONF_ACCESS_TOKEN])
        account = claims.get("sub")
        if not isinstance(account, str) or not account:
            return self.async_abort(reason="no_account")
        await self.async_set_unique_id(account)
        entry_data = {**data, **self._client}

        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data=entry_data
            )
        self._abort_if_unique_id_configured()
        email = claims.get("email")
        return self.async_create_entry(
            title=email if isinstance(email, str) and email else "weReci",
            data=entry_data,
        )
