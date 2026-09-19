"""Start a cook from the control panel: a recipe box, a screen picker, a switch.

The three entities only hold a choice; the Start button reads them and makes
the same call `wereci.show_cook_display` does.
"""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

# What show_cook_display can drive by entity. browser_mod browsers are left to
# the action: a busy install registers dozens of them.
SCREEN_PLATFORMS = ("cast", "androidtv", "androidtv_remote")


@dataclass
class PanelChoice:
    """What the panel's Start button will cook, and where."""

    recipe: str = ""
    screen: str | None = None  # entity_id
    without_phone: bool = True


def screens(hass: HomeAssistant) -> dict[str, str]:
    """Label → entity_id for every screen the action can drive."""
    found: dict[str, str] = {}
    for entry in er.async_get(hass).entities.values():
        if entry.domain != "media_player" or entry.platform not in SCREEN_PLATFORMS:
            continue
        if entry.disabled_by is not None:
            continue
        state = hass.states.get(entry.entity_id)
        label = (state.name if state else None) or entry.name or entry.original_name
        label = str(label or entry.entity_id)
        if label in found:
            label = f"{label} ({entry.entity_id})"
        found[label] = entry.entity_id
    return dict(sorted(found.items()))
