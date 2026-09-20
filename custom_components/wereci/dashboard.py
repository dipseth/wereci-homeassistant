"""The weReci dashboard — provided, not hand-built.

Three views per account, generated on every load (nothing is stored, read-only):

- a control panel, in sections: a picture-glance of what is cooking as the
  hero row, with the step and stop buttons along its foot (tap the picture for
  the live screen) and the step's text under it; below, the controls that
  start a cook and the ingredients as a to-do list that ticks the screen;
- the shopping list (List It) as a to-do list, the next tab over;
- the display view Cast devices and browser_mod browsers are shown: a single
  full-screen webpage card on `display_path`. Hidden from the tabs; reached
  by path.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, override

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace import dashboard
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_YAML
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.json import json_bytes, json_fragment
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.loader import async_get_integration

from .const import (
    CONF_CONTROL_PANEL,
    CONF_SHOPPING_LIST,
    DEFAULT_DASHBOARD_PATH,
    DOMAIN,
    ICON_MARK,
    ICONS_URL,
)

_LOGGER = logging.getLogger(__name__)

# The whole width of a section, however many columns it spans.
FULL = {"columns": "full"}


class CookDashboard(dashboard.LovelaceConfig):
    """A generated dashboard: one cook-display view per loaded account."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        super().__init__(hass, DEFAULT_DASHBOARD_PATH, {"mode": MODE_YAML})

    @property
    @override
    def mode(self) -> str:
        """YAML mode is what makes the frontend treat it as read-only."""
        return MODE_YAML

    def _build(self) -> dict[str, Any]:
        # HA's Cast receiver is served from another origin, so a relative card
        # URL would resolve there. Absolute, and external when there is one.
        try:
            base = get_url(self.hass, prefer_external=True)
        except NoURLAvailableError:
            base = ""
        views: list[dict[str, Any]] = []
        for entry in self.hass.config_entries.async_loaded_entries(DOMAIN):
            display = entry.runtime_data.cook_display
            url = base + display.display_path
            if entry.options.get(CONF_CONTROL_PANEL, True):
                views.append(self._control_view(entry))
                if entry.options.get(CONF_SHOPPING_LIST, True):
                    views.append(self._list_view(entry))
            views.append(
                {
                    "path": display.view_path,
                    "title": f"Cook display ({entry.title})",
                    "visible": False,
                    "panel": True,
                    "cards": [{"type": "iframe", "url": url, "aspect_ratio": "56%"}],
                }
            )
        return {"title": "weReci", "views": views}

    def _entity(self, entry: Any) -> Any:
        reg = er.async_get(self.hass)

        def entity(platform: str, key: str) -> str | None:
            return reg.async_get_entity_id(platform, DOMAIN, f"{entry.unique_id}_{key}")

        return entity

    def _control_view(self, entry: Any) -> dict[str, Any]:
        """The cook, laid out as sections: the picture is the hero.

        Its own row across the top, the step (or the search) as a line right
        under it; the controls and the ingredients share the row below. The
        picture already says what is cooking, so the text never repeats it.
        """
        entity = self._entity(entry)
        sensor = entity("sensor", "cooking")
        now = (
            f"{{% set s = '{sensor}' %}}"
            "{% if is_state(s, 'cooking') %}"
            "**Step {{ state_attr(s, 'step') }} of {{ state_attr(s, 'total_steps') }}**"
            "\n\n{{ state_attr(s, 'step_text') }}"
            "{% elif is_state(s, 'waiting') %}"
            "Open Cook Mode on your phone and enter **{{ state_attr(s, 'code') }}**."
            "{% else %}"
            f"{{% set m = '{entity('select', 'matches')}' %}}"
            "{% if state_attr(m, 'searching') %}"
            "Searching weReci for **{{ state_attr(m, 'query') }}**…"
            "{% elif state_attr(m, 'error') %}"
            "{{ state_attr(m, 'error') }}"
            "{% elif state_attr(m, 'recipe_id') %}"
            "Ready: **{{ states(m) }}**. Pick a screen and press **Start cooking**."
            "{% elif state_attr(m, 'matches') %}"
            "{{ state_attr(m, 'matches') | count }} recipes fit "
            "**{{ state_attr(m, 'query') }}** — pick one under **Matches**."
            "{% elif state_attr(m, 'query') %}"
            "Nothing found for **{{ state_attr(m, 'query') }}** yet — try other words."
            "{% else %}"
            "Type what you want to cook under **Recipe**."
            "{% endif %}"
            "{% endif %}"
        )
        controls = [
            {"entity": e, "name": name}
            for e, name in (
                (entity("text", "recipe"), "Recipe"),
                (entity("select", "matches"), "Matches"),
                (entity("select", "screen"), "Screen"),
                (entity("switch", "without_phone"), "Without a phone"),
                (entity("button", "start_cooking"), "Start cooking"),
            )
            if e
        ]
        return {
            "path": f"control-{entry.entry_id.lower()}",
            "title": entry.title,
            "icon": ICON_MARK,
            "type": "sections",
            "max_columns": 2,
            "dense_section_placement": True,
            "sections": [
                {
                    "type": "grid",
                    "column_span": 2,
                    # A section's grid is one column wide per column spanned,
                    # so both cards say so, or they sit side by side.
                    "cards": [
                        {**self._glance(entry, entity), "grid_options": FULL},
                        {"type": "markdown", "content": now, "grid_options": FULL},
                    ],
                },
                {
                    "type": "grid",
                    "cards": [
                        {"type": "heading", "heading": "Cook something"},
                        {"type": "entities", "entities": controls},
                    ],
                },
                # A stock to-do card over the ingredients entity: each box is
                # the same check-off as a tap on the kitchen screen. Nothing
                # to tick until a recipe is up, so the section waits for one.
                {
                    "type": "grid",
                    "visibility": [
                        {"condition": "state", "entity": sensor, "state": "cooking"}
                    ],
                    "cards": [
                        {"type": "heading", "heading": "Ingredients"},
                        {"type": "todo-list", "entity": entity("todo", "ingredients")},
                    ],
                },
            ],
        }

    def _list_view(self, entry: Any) -> dict[str, Any]:
        """The synced shopping list (List It), one tab over from the cook."""
        return {
            "path": f"list-{entry.entry_id.lower()}",
            "title": "Shopping list",
            "icon": "mdi:cart-outline",
            "cards": [
                {
                    "type": "todo-list",
                    "entity": self._entity(entry)("todo", "shopping_list"),
                    "title": "Shopping list",
                }
            ],
        }

    def _glance(self, entry: Any, entity: Any) -> dict[str, Any]:
        """The picture of what is cooking, with the step buttons along its foot.

        A stock card over real entities, so the same YAML works as a tile on
        anybody's own dashboard (README). Its `title` cannot be templated, so
        the recipe's name is left to the picture and the text under it.
        """
        display = entry.runtime_data.cook_display

        def press(key: str, icon: str) -> dict[str, Any]:
            button = entity("button", key)
            return {
                "entity": button,
                "icon": icon,
                "tap_action": {
                    "action": "perform-action",
                    "perform_action": "button.press",
                    "target": {"entity_id": button},
                },
            }

        sensor = entity("sensor", "cooking")
        return {
            "type": "picture-glance",
            "image_entity": entity("image", "cooking"),
            "aspect_ratio": "16:9",
            # The receiver itself is one tap away: scaling, swaps and Break it
            # down are taps on IT, which a picture cannot take.
            "tap_action": {
                "action": "navigate",
                "navigation_path": f"/{DEFAULT_DASHBOARD_PATH}/{display.view_path}",
            },
            "entities": [
                {"entity": sensor, "show_state": True, "tap_action": {"action": "none"}},
                press("previous_step", "mdi:chevron-left"),
                press("next_step", "mdi:chevron-right"),
                {
                    "entity": sensor,
                    "icon": "mdi:stop-circle-outline",
                    "tap_action": {
                        "action": "perform-action",
                        "perform_action": f"{DOMAIN}.stop_cook_display",
                        "data": {"config_entry_id": entry.entry_id},
                    },
                },
            ],
        }

    @override
    async def async_get_info(self) -> dict[str, Any]:
        """Describe the dashboard."""
        return {"mode": self.mode, "views": len(self._build()["views"])}

    @override
    async def async_load(self, force: bool) -> dict[str, Any]:
        """Build the config."""
        return self._build()

    @override
    async def async_json(self, force: bool) -> json_fragment:
        """Build the config, as JSON."""
        return json_fragment(json_bytes(self._build()))

    @callback
    def async_accounts_changed(self, setting_up: Any = None) -> None:
        """An account loaded or unloaded: open frontends should re-read.

        `setting_up` is the entry calling from its own setup — not counted as
        loaded yet, but about to be.
        """
        entries = self.hass.config_entries.async_loaded_entries(DOMAIN)
        if setting_up is not None:
            entries = [*entries, setting_up]
        _register_panel(
            self.hass,
            show_in_sidebar=any(
                entry.options.get(CONF_CONTROL_PANEL, True) for entry in entries
            ),
            update=True,
        )
        self._config_updated()


def _register_panel(hass: HomeAssistant, *, show_in_sidebar: bool, update: bool) -> None:
    # Hidden, never removed: the display view Cast devices load lives here too.
    frontend.async_register_built_in_panel(
        hass,
        "lovelace",
        frontend_url_path=DEFAULT_DASHBOARD_PATH,
        sidebar_title="weReci",
        sidebar_icon=ICON_MARK,
        require_admin=False,
        show_in_sidebar=show_in_sidebar,
        config={"mode": MODE_YAML},
        update=update,
    )


@callback
def async_setup_dashboard(hass: HomeAssistant) -> CookDashboard | None:
    """Register the dashboard, unless the user already owns that URL."""
    dashboards = hass.data[LOVELACE_DATA].dashboards
    if DEFAULT_DASHBOARD_PATH in dashboards:
        _LOGGER.info(
            "A dashboard at /%s already exists; weReci will use it as it is",
            DEFAULT_DASHBOARD_PATH,
        )
        return None
    cook = CookDashboard(hass)
    dashboards[DEFAULT_DASHBOARD_PATH] = cook
    _register_panel(hass, show_in_sidebar=True, update=False)
    return cook


async def async_setup_icons(hass: HomeAssistant) -> None:
    """Serve the weReci mark as an icon set, so the sidebar carries it."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(ICONS_URL, str(Path(__file__).parent / "frontend/icons.js"), True)]
    )
    # Cached hard; the version is what lets an update through.
    version = (await async_get_integration(hass, DOMAIN)).version
    # No frontend (a headless install, the tests): nothing to draw an icon in.
    if frontend.DATA_EXTRA_MODULE_URL in hass.data:
        frontend.add_extra_js_url(hass, f"{ICONS_URL}?v={version}")
