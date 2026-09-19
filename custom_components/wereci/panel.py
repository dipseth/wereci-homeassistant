"""Start a cook from the control panel: a recipe box, a screen picker, a switch.

The three entities only hold a choice; the Start button reads them and makes
the same call `wereci.show_cook_display` does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

# What show_cook_display can drive by entity. browser_mod browsers are left to
# the action: a busy install registers dozens of them.
SCREEN_PLATFORMS = ("cast", "androidtv", "androidtv_remote")


# A weReci recipe id, typed or pasted in place of a name.
RECIPE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{18,}$", re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    """One recipe the search found."""

    id: str
    title: str
    cuisine: str | None = None
    cookbook: str | None = None


@dataclass
class PanelChoice:
    """What the panel's Start button will cook, and where."""

    recipe: str = ""  # what was typed: words to search for, or an id
    screen: str | None = None  # entity_id
    without_phone: bool = True
    # The search the typed words started, and what came of it.
    searched: str | None = None  # the words `matches` answer; None = not yet
    searching: bool = False
    search_error: str | None = None
    matches: list[Match] = field(default_factory=list)
    match_id: str | None = None  # the pick — what Start cooks

    def labels(self) -> dict[str, str]:
        """Label → recipe id, for a dropdown. Twin titles are told apart."""
        found: dict[str, str] = {}
        for m in self.matches:
            label = m.title
            if label in found:
                label = f"{m.title} ({m.cookbook or m.cuisine or m.id[:8]})"
            if label in found:
                label = f"{m.title} ({m.id[:8]})"
            found[label] = m.id
        return found


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
