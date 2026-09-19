"""What every cook-display entity shares."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .cook_display import CookDisplay
from .coordinator import WereciConfigEntry


class CookDisplayEntity(Entity):
    """Pushed by the cook display; never polled."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, display: CookDisplay, entry: WereciConfigEntry, key: str) -> None:
        """Initialize."""
        self._display = display
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.unique_id))},
            name=f"weReci ({entry.title})",
            manufacturer="weReci",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://wereci.xyz",
        )

    async def async_added_to_hass(self) -> None:
        """Follow the session."""
        self.async_on_remove(
            self._display.async_add_listener(self.async_write_ha_state)
        )
