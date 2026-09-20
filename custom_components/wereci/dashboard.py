"""The weReci dashboard — provided, not hand-built.

Up to three views per account, generated on every load (nothing is stored,
read-only). The two pages below are a checkbox each, independent of one
another; with both off the account contributes only the hidden display view
and the dashboard leaves the sidebar:

- a control panel, in sections: while a display is up, the live receiver
  itself in an iframe on `display_path` (the same page the kitchen screen
  shows); when idle, the cooking picture with the search line under it. Below,
  the step buttons and the ingredients as a to-do list that ticks the screen
  (cooking only), and the controls that start a cook;
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
        """The cook, in sections: the live receiver itself is the hero.

        Two hero sections that never show together: while a display is up
        (waiting or cooking) an iframe on `display_path` shows the very page the
        kitchen screen shows; when idle, the cooking picture with the search
        line under it. Then the step buttons (cooking only), the controls that
        start a cook, and the ingredients (cooking only).

        The split is load-bearing. `display_path` is a stateless redirect, so
        once the iframe has followed it to the live receiver nothing would
        reload it when the cook ends — except the section hiding, which tears
        the card down, and rebuilding it on the way back in fetches afresh.
        The idle page refreshes itself every 10 s, so a newly started cook is
        picked up on its own.
        """
        entity = self._entity(entry)
        display = entry.runtime_data.cook_display
        sensor = entity("sensor", "cooking")
        matches = entity("select", "matches")
        search = (
            f"{{% set m = '{matches}' %}}"
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
        )
        controls = [
            {"entity": e, "name": name}
            for e, name in (
                (entity("text", "recipe"), "Recipe"),
                (matches, "Matches"),
                (entity("select", "screen"), "Screen"),
                (entity("switch", "without_phone"), "Without a phone"),
                (entity("button", "start_cooking"), "Start cooking"),
            )
            if e
        ]

        def when(**condition: str) -> list[dict[str, str]]:
            return [{"condition": "state", "entity": sensor, **condition}]

        def step(key: str, name: str, icon: str) -> dict[str, Any]:
            button = entity("button", key)
            return {
                "type": "tile",
                "entity": button,
                "name": name,
                "icon": icon,
                "vertical": True,
                "hide_state": True,
                "tap_action": {
                    "action": "perform-action",
                    "perform_action": "button.press",
                    "target": {"entity_id": button},
                },
            }

        return {
            "path": f"control-{entry.entry_id.lower()}",
            "title": entry.title,
            "icon": ICON_MARK,
            "type": "sections",
            "max_columns": 2,
            "sections": [
                {
                    "type": "grid",
                    "column_span": 2,
                    "visibility": when(state_not="idle"),
                    "cards": [
                        {
                            "type": "heading",
                            "heading": "Live cook display",
                            "heading_style": "title",
                            "icon": "mdi:television-play",
                            "badges": [{"type": "entity", "entity": sensor}],
                        },
                        # In a sections grid the iframe card IGNORES aspect_ratio
                        # and takes its height from grid rows alone — `rows: auto`
                        # collapses it to nothing (the 0.10.0 regression). Inside a
                        # stack the card is no longer grid-laid-out, so the aspect
                        # ratio sizes it, and the stack itself grows with content.
                        # Relative URL: this panel is same-origin with the view,
                        # unlike HA's Cast receiver, which is why the display view
                        # below needs the absolute form.
                        {
                            "type": "vertical-stack",
                            "cards": [
                                {
                                    "type": "iframe",
                                    "url": display.display_path,
                                    "aspect_ratio": "56%",
                                }
                            ],
                        },
                    ],
                },
                {
                    "type": "grid",
                    "column_span": 2,
                    "visibility": when(state="idle"),
                    "cards": [
                        # A stack: stacked whatever the section grid resolves to.
                        {
                            "type": "vertical-stack",
                            "cards": [
                                {
                                    "type": "picture-entity",
                                    "entity": entity("image", "cooking"),
                                    "image_entity": entity("image", "cooking"),
                                    "aspect_ratio": "16:9",
                                    "show_name": False,
                                    "show_state": False,
                                    "fit_mode": "cover",
                                },
                                {"type": "markdown", "text_only": True, "content": search},
                            ],
                        }
                    ],
                },
                {
                    "type": "grid",
                    "visibility": when(state="cooking"),
                    "cards": [
                        {"type": "heading", "heading": "Steps", "heading_style": "subtitle"},
                        {
                            "type": "horizontal-stack",
                            "cards": [
                                step("previous_step", "Back", "mdi:chevron-left"),
                                step("next_step", "Next", "mdi:chevron-right"),
                                # A service tile needs an entity to hang on.
                                {
                                    "type": "tile",
                                    "entity": sensor,
                                    "name": "Stop",
                                    "icon": "mdi:stop-circle-outline",
                                    "vertical": True,
                                    "hide_state": True,
                                    "tap_action": {
                                        "action": "perform-action",
                                        "perform_action": f"{DOMAIN}.stop_cook_display",
                                        "data": {"config_entry_id": entry.entry_id},
                                    },
                                },
                            ],
                        },
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
                    "visibility": when(state="cooking"),
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
            show_in_sidebar=any(_shows_a_page(entry) for entry in entries),
            update=True,
        )
        self._config_updated()


def _shows_a_page(entry: Any) -> bool:
    """Whether this account asks for either page.

    Neither is a dashboard with nothing on it but the hidden display view, so
    the sidebar entry comes off — the display view itself keeps being served,
    and a cook display goes on working.
    """
    return bool(
        entry.options.get(CONF_CONTROL_PANEL, True)
        or entry.options.get(CONF_SHOPPING_LIST, True)
    )


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
