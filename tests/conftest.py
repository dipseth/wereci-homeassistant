"""Fixtures for the weReci integration tests."""

import base64
import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wereci.const import DOMAIN

ACCOUNT = "tenant-abc"
EMAIL = "cook@example.test"


def make_token(sub: str = ACCOUNT, email: str = EMAIL) -> str:
    """An unsigned JWT-shaped token carrying the claims the flow reads."""
    body = base64.urlsafe_b64encode(json.dumps({"sub": sub, "email": email}).encode())
    return f"h.{body.decode().rstrip('=')}.s"


LIST = {
    "available": True,
    "seq": 7,
    "shared_with": ["partner"],
    "items": [
        {"key": "milk", "text": "1 pt milk", "checked": False, "aisle": "Dairy",
         "recipes": ["Carrot soup"], "added_by": ACCOUNT},
        {"key": "eggs", "text": "6 eggs", "checked": True, "aisle": None,
         "recipes": [], "added_by": "partner"},
    ],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/."""
    return


@pytest.fixture
def entry() -> MockConfigEntry:
    """A signed-in config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT,
        title=EMAIL,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": make_token(),
                "refresh_token": "r",
                "expires_at": time.time() + 3600,
                "token_type": "Bearer",
            },
            "base_url": "https://wereci.xyz",
            "client_id": "client-1",
            "authorize_url": "https://wereci.xyz/oauth/authorize",
            "token_url": "https://wereci.xyz/api/oauth/token",
        },
    )


@pytest.fixture
def list_call():
    """Stand in for the MCP round trip the list coordinator makes."""
    with (
        patch(
            "custom_components.wereci.coordinator.WereciListCoordinator._call",
            new_callable=AsyncMock,
            return_value=LIST,
        ) as call,
        patch(
            "custom_components.wereci.llm_api.WereciToolsCoordinator._async_update_data",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        yield call
