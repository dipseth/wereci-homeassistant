"""The control panel's “without a phone” switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import WereciConfigEntry
from .entity import CookDisplayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch."""
    async_add_entities(
        [WithoutPhoneSwitch(entry.runtime_data.cook_display, entry, "without_phone")]
    )


class WithoutPhoneSwitch(CookDisplayEntity, SwitchEntity, RestoreEntity):
    """On: Home Assistant runs the cook. Off: the phone gets the link."""

    _attr_icon = "mdi:cellphone-off"

    @property
    def is_on(self) -> bool:
        """Whether the panel starts phone-free cooks."""
        return self._display.panel.without_phone

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Cook from Home Assistant."""
        self._display.panel.without_phone = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Cook from the phone."""
        self._display.panel.without_phone = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Remember the choice across restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in ("on", "off"):
            self._display.panel.without_phone = last.state == "on"
