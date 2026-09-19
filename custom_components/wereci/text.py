"""The control panel's recipe box."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
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
    """Add the recipe box."""
    async_add_entities([RecipeText(entry.runtime_data.cook_display, entry, "recipe")])


class RecipeText(CookDisplayEntity, TextEntity, RestoreEntity):
    """A recipe's name, or its id."""

    _attr_native_max = 200
    _attr_icon = "mdi:book-open-variant"

    @property
    def native_value(self) -> str:
        """The recipe to cook."""
        return self._display.panel.recipe

    async def async_set_value(self, value: str) -> None:
        """Take a name or an id."""
        self._display.panel.recipe = value.strip()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Remember the last recipe across restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state not in ("unknown", "unavailable"):
            self._display.panel.recipe = last.state
