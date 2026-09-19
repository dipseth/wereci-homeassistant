"""The control panel's screen picker."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import WereciConfigEntry
from .entity import CookDisplayEntity
from .panel import screens


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the screen picker."""
    async_add_entities([ScreenSelect(entry.runtime_data.cook_display, entry, "screen")])


class ScreenSelect(CookDisplayEntity, SelectEntity, RestoreEntity):
    """Every Cast device, Android TV and Fire TV the action can drive."""

    _attr_icon = "mdi:monitor"

    @property
    def options(self) -> list[str]:
        """Read live: screens come and go, and names load after startup."""
        return list(screens(self.hass))

    @property
    def current_option(self) -> str | None:
        """The chosen screen, by its label."""
        chosen = self._display.panel.screen
        return next(
            (label for label, eid in screens(self.hass).items() if eid == chosen), None
        )

    async def async_select_option(self, option: str) -> None:
        """Choose a screen."""
        self._display.panel.screen = screens(self.hass).get(option)
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """The entity behind the label — what restores the choice."""
        return {"screen_entity_id": self._display.panel.screen}

    async def async_added_to_hass(self) -> None:
        """Remember the last screen across restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.attributes.get("screen_entity_id"):
            self._display.panel.screen = last.attributes["screen_entity_id"]

        # The options a dashboard shows are a state attribute, so re-publish
        # when screens are added or renamed, and once names exist at startup.
        @callback
        def refresh(_: Event) -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, refresh)
        )
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STARTED, refresh)
        )
