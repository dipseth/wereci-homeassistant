"""Next / previous step on the weReci cook display."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cook_display import STATUS_COOKING, CookDisplay
from .coordinator import WereciConfigEntry
from .entity import CookDisplayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the step buttons."""
    display = entry.runtime_data.cook_display
    async_add_entities(
        [
            StepButton(display, entry, "next_step", 1),
            StepButton(display, entry, "previous_step", -1),
            StartButton(display, entry, "start_cooking"),
        ]
    )


class StepButton(CookDisplayEntity, ButtonEntity):
    """Sends intent only — the phone moves the step and repaints the screen."""

    def __init__(
        self, display: CookDisplay, entry: WereciConfigEntry, key: str, delta: int
    ) -> None:
        """Initialize."""
        super().__init__(display, entry, key)
        self._delta = delta

    @property
    def available(self) -> bool:
        """Only while something is cooking."""
        return self._display.status == STATUS_COOKING

    async def async_press(self) -> None:
        """Step."""
        await self._display.async_command({"do": "step", "delta": self._delta})


class StartButton(CookDisplayEntity, ButtonEntity):
    """Cook the panel's recipe on the panel's screen."""

    _attr_icon = "mdi:play-circle-outline"

    def __init__(self, display: CookDisplay, entry: WereciConfigEntry, key: str) -> None:
        """Initialize."""
        super().__init__(display, entry, key)
        self._entry = entry

    async def async_press(self) -> None:
        """Start."""
        # Imported here: services.py imports the platforms' shared modules.
        from .services import async_start_from_panel  # noqa: PLC0415

        await async_start_from_panel(self.hass, self._entry.runtime_data)
