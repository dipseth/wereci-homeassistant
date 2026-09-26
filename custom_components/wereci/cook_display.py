"""Cook Mode on a Home Assistant screen.

Home Assistant is the DISPLAY side of weReci's pair-by-code cast relay: it opens
the channel, puts the receiver page on a screen, and hands the pairing code to
the phone. The phone stays the sender — this module never sees a recipe, only
the opaque snapshot the phone pushes and the intent-only commands going back.

The receiver token is a bearer for one ephemeral channel. It is never logged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import json
import logging
import re
import secrets
from typing import Any
from urllib.parse import quote

from aiohttp import web
import httpx

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.httpx_client import get_async_client

from .coordinator import tag_along_list_refresh
from .const import (
    CAST_API_PATH,
    CAST_POLL_TIMEOUT,
    CAST_RECEIVER_PATH,
    CAST_REQUEST_TIMEOUT,
    CONF_DISPLAY_SECRET,
    DOMAIN,
    TOOL_GET_RECIPE,
    TOOL_SEARCH_RECIPES,
)
from .panel import RECIPE_ID, Match, PanelChoice
from .sender import HaSender

_LOGGER = logging.getLogger(__name__)

STATUS_IDLE = "idle"
STATUS_WAITING = "waiting"
STATUS_COOKING = "cooking"

_RETRY_SECONDS = 2


@dataclass
class DisplayTarget:
    """Where the receiver was put, and so how to take it down again."""

    kind: str  # cast | browser | androidtv | androidtv_remote
    entity_id: str | None = None
    browser_id: str | None = None
    return_path: str = "/"


@dataclass
class Recipe:
    """The recipe a display was opened for."""

    id: str
    title: str


@dataclass
class CookSession:
    """One open relay channel."""

    code: str
    token: str
    target: DisplayTarget
    recipe: Recipe | None = None
    sender: HaSender | None = None
    paired: bool = False
    snapshot: dict[str, Any] | None = None
    version: int = 0
    listeners: list[Callable[[], None]] = field(default_factory=list, repr=False)


class CookDisplay:
    """The cook display of one weReci account."""

    def __init__(self, hass: HomeAssistant, entry: Any, base_url: str) -> None:
        """Initialize."""
        self.hass = hass
        self._entry = entry
        self._base_url = base_url.rstrip("/")
        self._session: CookSession | None = None
        self._task: asyncio.Task[None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self.panel = PanelChoice()
        self._search: asyncio.Task[None] | None = None

    # ── the panel's search ─────────────────────────────────────────────────

    @callback
    def async_search(
        self, call_tool: Callable[[str, dict[str, Any]], Any], query: str
    ) -> None:
        """Typed words → matches, in the background; the entities follow along.

        A search takes seconds, so the text box does not wait for it. An id
        needs no search: it is the pick.
        """
        if self._search is not None:
            self._search.cancel()
            self._search = None
        panel = self.panel
        is_id = bool(RECIPE_ID.match(query))
        panel.recipe = query
        panel.matches, panel.search_error = [], None
        panel.match_id = query if is_id else None
        panel.searched = None
        panel.searching = bool(query) and not is_id
        self._notify()
        if panel.searching:
            self._search = self._entry.async_create_background_task(
                self.hass, self._async_run_search(call_tool, query), f"{DOMAIN} search"
            )

    async def _async_run_search(
        self, call_tool: Callable[[str, dict[str, Any]], Any], query: str
    ) -> None:
        panel = self.panel
        try:
            panel.matches = await search_matches(call_tool, query)
        except HomeAssistantError as err:
            panel.search_error = str(err)
        # Cancelled (newer words were typed): that search owns the panel now.
        panel.searched, panel.searching = query, False
        # Pre-pick only when the words name one recipe. A vague word ("pasta")
        # fits several titles: that choice is the user's.
        named = [m for m in panel.matches if resembles(query, m.title)]
        exact = [m for m in named if m.title.strip().lower() == query.lower()]
        panel.match_id = (
            exact[0].id if exact else named[0].id if len(named) == 1 else None
        )
        self._notify()

    async def async_search_settled(self) -> None:
        """Wait out a search that is still running."""
        if self._search is not None and not self._search.done():
            await asyncio.wait([self._search])

    @callback
    def async_pick(self, recipe_id: str | None) -> None:
        """Choose one of the matches."""
        self.panel.match_id = recipe_id
        self._notify()

    # ── state the entities read ────────────────────────────────────────────

    @property
    def session(self) -> CookSession | None:
        """The open channel, if any."""
        return self._session

    @property
    def status(self) -> str:
        """idle → waiting (code out, no phone yet) → cooking."""
        if self._session is None:
            return STATUS_IDLE
        return STATUS_COOKING if self._session.snapshot else STATUS_WAITING

    @property
    def base_url(self) -> str:
        """The weReci site this account lives on."""
        return self._base_url

    @property
    def display_path(self) -> str:
        """The stable local URL a webpage card points at."""
        return f"/api/{DOMAIN}/display/{self._entry.data[CONF_DISPLAY_SECRET]}"

    @property
    def view_path(self) -> str:
        """This account's view in the generated dashboard."""
        return f"display-{self._entry.entry_id.lower()}"

    @property
    def receiver_url(self) -> str | None:
        """The live receiver URL. Holds the token — never log it."""
        if self._session is None:
            return None
        return (
            f"{self._base_url}{CAST_RECEIVER_PATH}"
            f"?rt={self._session.token}&code={self._session.code}"
        )

    @property
    def pair_link(self) -> str | None:
        """What the phone opens to pair without typing."""
        if self._session is None:
            return None
        recipe = self._session.recipe
        if recipe is None:
            return f"{self._base_url}/?cast={self._session.code}"
        # ?cook=1 is the app's own "open Cook Mode" link, so the tap lands in
        # Cook Mode on this recipe and pairs with nothing left to do.
        return (
            f"{self._base_url}/recipe/{quote(recipe.id, safe='')}"
            f"?cook=1&cast={self._session.code}"
        )

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        """Tell an entity when the session changes."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    # ── the relay ──────────────────────────────────────────────────────────

    async def _open_channel(self) -> tuple[str, str]:
        client = get_async_client(self.hass)
        try:
            res = await client.post(
                f"{self._base_url}{CAST_API_PATH}/channel",
                timeout=CAST_REQUEST_TIMEOUT,
            )
            res.raise_for_status()
            data = res.json()
        except (httpx.HTTPError, ValueError) as err:
            raise HomeAssistantError("weReci could not open a cook display") from err
        finally:
            tag_along_list_refresh(self.hass, self._entry)
        code, token = data.get("code"), data.get("token")
        if not isinstance(code, str) or not isinstance(token, str):
            raise HomeAssistantError("weReci could not open a cook display")
        return code, token

    async def async_show(
        self,
        target: DisplayTarget,
        *,
        dashboard_path: str,
        view_path: str,
        notify: list[str] | None,
        recipe: Recipe | None = None,
        sender: HaSender | None = None,
    ) -> dict[str, str]:
        """Open a channel, put the receiver on `target`, hand the code over."""
        if self._session is not None:
            await self.async_stop()
        code, token = await self._open_channel()
        self._session = CookSession(
            code=code, token=token, target=target, recipe=recipe, sender=sender
        )
        try:
            if sender is not None:
                # Paired and on step 1 before the screen even loads, so the
                # receiver never shows a code nobody is going to type.
                await sender.async_start(
                    code,
                    lambda coro: self._entry.async_create_background_task(
                        self.hass, coro, f"{DOMAIN} cook sender"
                    ),
                )
            await self._put_on_screen(target, dashboard_path, view_path)
        except Exception:
            await self.async_stop(restore=False)
            raise
        self._task = self._entry.async_create_background_task(
            self.hass, self._poll(self._session), f"{DOMAIN} cook display"
        )
        self._notify()
        if sender is None:
            await self._notify_phones(notify)
        shown = {"code": code, "link": self.pair_link or ""}
        if recipe is not None:
            shown.update(recipe_id=recipe.id, title=recipe.title)
        return shown

    async def _put_on_screen(
        self, target: DisplayTarget, dashboard_path: str, view_path: str
    ) -> None:
        call = self.hass.services.async_call
        if target.kind == "cast":
            # HA's own Cast receiver shows a dashboard view whose webpage card
            # points at `display_path`, which redirects to the live receiver.
            await call(
                "cast",
                "show_lovelace_view",
                {
                    "entity_id": target.entity_id,
                    "dashboard_path": dashboard_path,
                    "view_path": view_path,
                },
                blocking=True,
            )
        elif target.kind == "browser":
            data = {"browser_id": target.browser_id}
            await call(
                "browser_mod",
                "navigate",
                {**data, "path": f"/{dashboard_path}/{view_path}"},
                blocking=True,
            )
        elif target.kind == "androidtv":
            await call(
                "androidtv",
                "adb_command",
                {
                    "entity_id": target.entity_id,
                    "command": (
                        "am start -a android.intent.action.VIEW "
                        f"-d '{self.receiver_url}'"
                    ),
                },
                blocking=True,
            )
        else:  # androidtv_remote
            await call(
                "media_player",
                "play_media",
                {
                    "entity_id": target.entity_id,
                    "media_content_type": "url",
                    "media_content_id": self.receiver_url,
                },
                blocking=True,
            )

    async def _restore_screen(self, target: DisplayTarget) -> None:
        call = self.hass.services.async_call
        try:
            if target.kind == "cast":
                await call(
                    "media_player", "turn_off", {"entity_id": target.entity_id}
                )
            elif target.kind == "browser":
                await call(
                    "browser_mod",
                    "navigate",
                    {"browser_id": target.browser_id, "path": target.return_path},
                )
            elif target.kind == "androidtv":
                await call(
                    "androidtv",
                    "adb_command",
                    {"entity_id": target.entity_id, "command": "HOME"},
                )
        except HomeAssistantError as err:
            _LOGGER.debug("Could not restore the cook display screen: %s", err)

    async def _notify_phones(self, services: list[str] | None) -> None:
        session = self._session
        if session is None:
            return
        registered = self.hass.services.async_services().get("notify", {})
        if services is None:
            names = [n for n in registered if n.startswith("mobile_app_")]
        else:
            names = [s.removeprefix("notify.") for s in services]
        link = self.pair_link
        for name in names:
            if name not in registered:
                _LOGGER.warning("weReci cook display: no notify.%s service", name)
                continue
            await self.hass.services.async_call(
                "notify",
                name,
                {
                    "title": "weReci",
                    "message": (
                        f"Tap to cook {session.recipe.title} on the kitchen "
                        f"screen — or enter {session.code} in Cook Mode."
                        if session.recipe
                        else "The kitchen screen is ready. Tap, then open Cook "
                        f"Mode — or enter {session.code}."
                    ),
                    # iOS reads `url`, Android `clickAction`.
                    "data": {"url": link, "clickAction": link, "tag": "wereci-cook"},
                },
            )

    async def _poll(self, session: CookSession) -> None:
        """Long-poll the channel for whatever the phone pushes."""
        client = get_async_client(self.hass)
        url = f"{self._base_url}{CAST_API_PATH}/channel"
        while self._session is session:
            try:
                res = await client.get(
                    url,
                    params={"token": session.token, "since": session.version},
                    timeout=CAST_POLL_TIMEOUT,
                )
            except httpx.HTTPError:
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            if res.status_code == 404:
                # The phone hung up, or nobody paired before the code expired.
                if self._session is session:
                    self._session = None
                    if session.sender is not None:
                        session.sender.stop()
                    self._notify()
                    await self._restore_screen(session.target)
                return
            if res.status_code != 200:
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            try:
                data = res.json()
            except ValueError:
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            changed = bool(data.get("paired")) != session.paired
            session.paired = bool(data.get("paired"))
            if isinstance(data.get("version"), int):
                session.version = data["version"]
            if isinstance(data.get("state"), str):
                try:
                    snapshot = json.loads(data["state"])
                except ValueError:
                    snapshot = None
                if isinstance(snapshot, dict):
                    session.snapshot = snapshot
                    changed = True
            if changed and self._session is session:
                self._notify()

    async def async_command(self, cmd: dict[str, Any]) -> None:
        """Send intent to the phone, exactly as the TV's own D-pad would."""
        session = self._session
        if session is None or session.snapshot is None:
            raise ServiceValidationError("Nothing is cooking on a weReci display")
        try:
            res = await get_async_client(self.hass).post(
                f"{self._base_url}{CAST_API_PATH}/command",
                json={"token": session.token, "cmd": cmd},
                timeout=CAST_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as err:
            raise HomeAssistantError("weReci could not be reached") from err
        finally:
            tag_along_list_refresh(self.hass, self._entry)
        if res.status_code != 200:
            raise HomeAssistantError("The weReci cook display is no longer connected")

    async def async_stop(self, *, restore: bool = True) -> None:
        """Close the channel and give the screen back."""
        session, self._session = self._session, None
        if session is not None and session.sender is not None:
            session.sender.stop()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if session is None:
            return
        self._notify()
        try:
            await get_async_client(self.hass).delete(
                f"{self._base_url}{CAST_API_PATH}/channel",
                params={"token": session.token},
                timeout=CAST_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError:
            pass  # the channel expires on its own
        finally:
            tag_along_list_refresh(self.hass, self._entry)
        if restore:
            await self._restore_screen(session.target)


_WORD = re.compile(r"[^\W_]+", re.UNICODE)
# Words that say nothing about WHICH recipe.
_FILLER = frozenset(
    "a an and the of for with my our that this some recipe recipes".split()
)


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.casefold()) if w not in _FILLER]


MAX_MATCHES = 8


def cookable_hits(found: dict[str, Any]) -> list[dict[str, Any]]:
    """Search hits that are recipes. `reference` hits are encyclopedia cards."""
    return [
        h
        for h in found.get("hits") or []
        if isinstance(h, dict) and h.get("id") and not h.get("reference")
    ]


async def search_matches(
    call_tool: Callable[[str, dict[str, Any]], Any], query: str
) -> list[Match]:
    """The panel's search: what weReci has for these words, nearest first."""
    found = await call_tool(
        TOOL_SEARCH_RECIPES, {"query": query, "limit": MAX_MATCHES}
    )
    return [
        Match(
            str(h["id"]),
            str(h.get("title") or "Untitled"),
            h.get("cuisine") or None,
            h.get("cookbook") or None,
        )
        for h in cookable_hits(found)
    ]


def resembles(query: str, title: str) -> bool:
    """Does this title look like what was asked for?

    weReci's search is semantic: it always answers with the NEAREST recipe,
    even when nothing fits — "lasagna" finds the closest pasta. A kitchen
    screen should not start cooking a guess, so at least half of the asked
    words must appear in the title (prefixes count: "carrot" ~ "carrots").
    """
    asked, have = _words(query), _words(title)
    if not asked:
        return False
    hits = sum(
        any(h.startswith(a) or a.startswith(h) for h in have if min(len(a), len(h)) >= 3)
        for a in asked
    )
    return hits * 2 >= len(asked)


async def resolve_recipe(
    call_tool: Callable[[str, dict[str, Any]], Any],
    recipe_id: str | None,
    query: str | None,
    allow_closest: bool = False,
) -> Recipe | None:
    """An id, or a name to search for → the recipe to open."""
    if recipe_id and query:
        raise ServiceValidationError("Give recipe_id or recipe, not both")
    if query:
        found = await call_tool(TOOL_SEARCH_RECIPES, {"query": query, "limit": 5})
        hits = cookable_hits(found)
        hit = next(
            (
                h
                for h in hits
                if allow_closest or resembles(query, str(h.get("title") or ""))
            ),
            None,
        )
        if hit is None:
            near = ", ".join(f"“{h.get('title')}”" for h in hits[:3])
            raise ServiceValidationError(
                f"No weReci recipe is called “{query}”."
                + (f" Closest: {near}. Use recipe_id, or allow_closest." if near else "")
            )
        return Recipe(str(hit["id"]), str(hit.get("title") or query))
    if recipe_id:
        got = await call_tool(TOOL_GET_RECIPE, {"id": recipe_id})
        if got.get("error"):
            raise ServiceValidationError(f"weReci has no recipe {recipe_id}")
        return Recipe(recipe_id, str(got.get("title") or got.get("recipe_title") or "your recipe"))
    return None


async def resolve_sender(
    hass: HomeAssistant,
    call_tool: Callable[[str, dict[str, Any]], Any],
    base_url: str,
    recipe: Recipe | None,
    on_permission_needed: Callable[[], None] | None = None,
    request: Callable[[str, str, dict[str, Any] | None], Any] | None = None,
) -> HaSender:
    """without_phone: fetch the whole recipe; Home Assistant will run the cook."""
    if recipe is None:
        raise ServiceValidationError("without_phone needs a recipe or recipe_id")
    got = await call_tool(TOOL_GET_RECIPE, {"id": recipe.id})
    sender = HaSender(
        hass,
        base_url.rstrip("/"),
        {**got, "id": recipe.id},
        lambda: None,
        request=request,
        on_permission_needed=on_permission_needed,
    )
    if got.get("error") or not sender.has_steps:
        raise ServiceValidationError(f"{recipe.title} has no steps to cook through")
    return sender


def resolve_target(
    hass: HomeAssistant,
    entity_id: str | None,
    browser_id: str | None,
    return_path: str,
) -> DisplayTarget:
    """A service call's target → how to drive that kind of screen."""
    if bool(entity_id) == bool(browser_id):
        raise ServiceValidationError("Give either entity_id or browser_id")
    if browser_id:
        if not hass.services.has_service("browser_mod", "navigate"):
            raise ServiceValidationError("browser_id needs the browser_mod integration")
        return DisplayTarget("browser", browser_id=browser_id, return_path=return_path)
    reg = er.async_get(hass).async_get(entity_id or "")
    platform = reg.platform if reg else None
    if platform in ("cast", "androidtv", "androidtv_remote"):
        return DisplayTarget(platform, entity_id=entity_id)
    raise ServiceValidationError(
        f"{entity_id} is not a Cast, Android TV or Fire TV media player"
    )


def ensure_display_secret(hass: HomeAssistant, entry: Any) -> None:
    """Mint the unguessable path segment of `display_path`, once per entry."""
    if CONF_DISPLAY_SECRET not in entry.data:
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_DISPLAY_SECRET: secrets.token_urlsafe(18)},
        )


_IDLE_PAGE = (
    "<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=10>"
    "<body style='margin:0;display:grid;place-items:center;height:100vh;"
    "background:#1c1917;color:#fde68a;font:4vh system-ui'>"
    "weReci — no cook display is active"
)


class CookDisplayView(HomeAssistantView):
    """`display_path`: redirect a webpage card to the live receiver.

    Unauthenticated because an iframe — above all one inside HA's Cast receiver
    — cannot carry a bearer; the secret path segment is the credential, and all
    it yields is the same ephemeral receiver URL already on the screen.
    """

    url = f"/api/{DOMAIN}/display/{{secret}}"
    name = f"api:{DOMAIN}:display"
    requires_auth = False

    async def get(self, request: web.Request, secret: str) -> web.Response:
        """Redirect to the receiver, or say nothing is on."""
        hass: HomeAssistant = request.app["hass"]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if secrets.compare_digest(
                str(entry.data.get(CONF_DISPLAY_SECRET, "")), secret
            ):
                target = entry.runtime_data.cook_display.receiver_url
                if target:
                    raise web.HTTPFound(target)
                break
        return web.Response(
            text=_IDLE_PAGE,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )
