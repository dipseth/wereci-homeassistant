"""Sign-in: dynamic registration, PKCE, one entry per account."""

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow

from custom_components.wereci.const import DOMAIN

from .conftest import ACCOUNT, EMAIL, make_token

META = {
    "authorize_url": "https://wereci.xyz/oauth/authorize",
    "token_url": "https://wereci.xyz/api/oauth/token",
    "registration_url": "https://wereci.xyz/api/oauth/register",
}


async def test_full_flow(
    hass: HomeAssistant, hass_client_no_auth, aioclient_mock, current_request_with_host
) -> None:
    with (
        patch("custom_components.wereci.config_flow.async_discover_oauth", return_value=META),
        patch(
            "custom_components.wereci.config_flow.async_register_client",
            return_value="client-1",
        ) as register,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    url = result["url"]
    assert url.startswith("https://wereci.xyz/oauth/authorize?")
    assert "client_id=client-1" in url
    assert "code_challenge=" in url and "code_challenge_method=S256" in url
    assert "scope=recipes:read+list:sync" in url
    assert register.await_args.args[1] == META["registration_url"]

    state = config_entry_oauth2_flow._encode_jwt(
        hass,
        {"flow_id": result["flow_id"], "redirect_uri": "https://example.com/auth/external/callback"},
    )
    client = await hass_client_no_auth()
    resp = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert resp.status == 200

    aioclient_mock.post(
        META["token_url"],
        json={
            "access_token": make_token(),
            "refresh_token": "r",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    with patch("custom_components.wereci.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["result"].unique_id == ACCOUNT
    assert result["data"]["client_id"] == "client-1"
    # The token request proved possession with the PKCE verifier, no secret.
    sent = aioclient_mock.mock_calls[-1][2]
    assert sent["client_id"] == "client-1" and "code_verifier" in sent
    assert "client_secret" not in sent


async def test_registration_refused_aborts(hass: HomeAssistant, current_request_with_host) -> None:
    from custom_components.wereci.api import WereciRegistrationError

    with (
        patch("custom_components.wereci.config_flow.async_discover_oauth", return_value=META),
        patch(
            "custom_components.wereci.config_flow.async_register_client",
            side_effect=WereciRegistrationError("redirect_uri not allowed"),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "registration_refused"
