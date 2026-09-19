"""What is cooking on the weReci display."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .cook_display import STATUS_COOKING, STATUS_IDLE, STATUS_WAITING
from .coordinator import WereciConfigEntry
from .entity import CookDisplayEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the cooking sensor."""
    async_add_entities([CookingSensor(entry.runtime_data.cook_display, entry, "cooking")])


class CookingSensor(CookDisplayEntity, SensorEntity):
    """idle → waiting (code out, no phone yet) → cooking."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_IDLE, STATUS_WAITING, STATUS_COOKING]

    @property
    def native_value(self) -> str:
        """The session's status."""
        return self._display.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The step on the screen; the code while it is still worth typing."""
        attrs: dict[str, Any] = {"display_path": self._display.display_path}
        session = self._display.session
        if session is None:
            return attrs
        attrs["driven_by"] = "home_assistant" if session.sender else "phone"
        if session.recipe:
            attrs["recipe_id"] = session.recipe.id
        if not session.paired:
            attrs["code"] = session.code
            attrs["link"] = self._display.pair_link
        snap = session.snapshot
        if snap:
            idx = snap.get("stepIdx")
            attrs.update(
                title=snap.get("title"),
                step=idx + 1 if isinstance(idx, int) else None,
                total_steps=snap.get("totalSteps"),
                step_text=snap.get("stepText"),
                component=snap.get("componentLabel"),
                scale=snap.get("scaleLabel"),
                ingredients=[
                    {"text": i.get("text"), "checked": bool(i.get("checked"))}
                    for i in snap.get("allIngredients") or []
                    if isinstance(i, dict)
                ],
            )
        return attrs
