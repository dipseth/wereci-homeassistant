"""The picture of what is cooking: what a picture-glance card shows.

The recipe's own photo while it cooks. Everything else is a card drawn here:
the weReci mark when idle, the pairing code while waiting for a phone, the
title when a recipe has no photo (or it would not load). The picture changes
with the recipe, not with the step — an image entity is refetched whenever
`image_last_updated` moves, so it only moves when there is something new to see.
"""

from __future__ import annotations

from html import escape
import ipaddress
import logging
import textwrap
from urllib.parse import urljoin, urlsplit

import httpx

from homeassistant.components.image import (
    Image,
    ImageContentTypeError,
    ImageEntity,
    infer_image_type,
    valid_image_content_type,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CAST_REQUEST_TIMEOUT
from .cook_display import STATUS_COOKING, STATUS_WAITING, CookDisplay
from .coordinator import WereciConfigEntry
from .entity import CookDisplayEntity

_LOGGER = logging.getLogger(__name__)

_MAX_REDIRECTS = 3
_MAX_BYTES = 8 * 1024 * 1024
# Names that only resolve inside somebody's network.
_PRIVATE_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")

# The mark's three solid rings (frontend/icons.js), in the brand's dark-mode inks.
_MARK = (
    '<g fill="none" stroke-width="8">'
    '<circle cx="39.61" cy="56" r="19" stroke="#5E9A87"/>'
    '<circle cx="60.39" cy="56" r="19" stroke="#9C8A45"/>'
    '<circle cx="50" cy="38" r="19" stroke="#D4907B"/>'
    "</g>"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WereciConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the cooking picture."""
    async_add_entities(
        [CookingImage(hass, entry.runtime_data.cook_display, entry, "cooking")]
    )


def safe_photo_url(base_url: str, url: object) -> str | None:
    """A photo URL Home Assistant is willing to fetch, or None.

    On a phone-driven cook the URL arrives in a snapshot, from whoever typed
    the pairing code. It is fetched from INSIDE the home network, so it must
    be https and name a public host — never an address or a local name.
    """
    if not isinstance(url, str) or not url:
        return None
    parts = urlsplit(urljoin(f"{base_url}/", url))
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or "." not in host or host.endswith(_PRIVATE_SUFFIXES):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return parts.geturl()
    return None


def draw_card(headline: str, caption: str = "") -> bytes:
    """A 16:9 card in the receiver's colours: the mark, and a few words.

    Everything sits in the top four fifths: picture-glance lays its row of
    buttons over the foot of the image.
    """
    lines = textwrap.wrap(headline, width=22)[:3] or [""]
    size = 84 if len(lines) == 1 else 68
    top = 400 if len(lines) == 1 else 370
    text = "".join(
        f'<text x="640" y="{top + i * size * 1.15:.0f}" font-size="{size}" '
        f'font-weight="600" fill="#fde68a">{escape(line)}</text>'
        for i, line in enumerate(lines)
    )
    if caption:
        text += (
            f'<text x="640" y="550" font-size="34" fill="#a8a29e">'
            f"{escape(caption)}</text>"
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
        'font-family="system-ui, sans-serif" text-anchor="middle">'
        '<rect width="1280" height="720" fill="#1c1917"/>'
        f'<g transform="translate(490 0) scale(3)">{_MARK}</g>'
        f"{text}</svg>"
    ).encode()


class CookingImage(CookDisplayEntity, ImageEntity):
    """idle: the mark → waiting: the code → cooking: the recipe's photo."""

    def __init__(
        self, hass: HomeAssistant, display: CookDisplay, entry: WereciConfigEntry, key: str
    ) -> None:
        """Initialize."""
        CookDisplayEntity.__init__(self, display, entry, key)
        ImageEntity.__init__(self, hass, verify_ssl=True)
        self._shown = self._picture()
        self._attr_image_last_updated = dt_util.utcnow()

    def _picture(self) -> tuple[str, str | None, str | None]:
        """What the picture depends on: (status, photo URL, words)."""
        session = self._display.session
        status = self._display.status
        if session is None:
            return (status, None, None)
        if status == STATUS_WAITING:
            return (status, None, session.code)
        snap = session.snapshot or {}
        title = snap.get("title") or (session.recipe.title if session.recipe else None)
        photo = safe_photo_url(self._display.base_url, snap.get("photoUrl"))
        return (status, photo, str(title) if title else None)

    async def async_added_to_hass(self) -> None:
        """Follow the session."""
        self.async_on_remove(self._display.async_add_listener(self._session_changed))

    @callback
    def _session_changed(self) -> None:
        # A step, a tick, a swap: same picture. Do not make frontends refetch.
        if (picture := self._picture()) == self._shown:
            return
        self._shown = picture
        self._cached_image = None
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """The photo, or the card that stands in for one."""
        if self._cached_image is None:
            shown = self._shown
            image = await self._render(*shown)
            if shown != self._shown:
                return image.content  # moved on while loading; not worth keeping
            self._cached_image = image
            self._attr_content_type = image.content_type
        return self._cached_image.content

    async def _render(self, status: str, photo: str | None, words: str | None) -> Image:
        if photo and (image := await self._fetch(photo)) is not None:
            return image
        if status == STATUS_WAITING:
            card = draw_card(words or "", "Open Cook Mode and enter this code")
        elif status == STATUS_COOKING:
            card = draw_card(words or "Cooking")
        else:
            card = draw_card("Nothing is cooking")
        return Image(content_type="image/svg+xml", content=card)

    async def _fetch(self, url: str) -> Image | None:
        """GET the photo, re-checking every hop a redirect takes it to."""
        base = self._display.base_url
        try:
            for _ in range(_MAX_REDIRECTS + 1):
                res = await self._client.get(
                    url, timeout=CAST_REQUEST_TIMEOUT, follow_redirects=False
                )
                if not res.is_redirect:
                    break
                hop = safe_photo_url(base, urljoin(url, res.headers.get("location", "")))
                if hop is None:
                    return None
                url = hop
            else:
                return None
            res.raise_for_status()
        except httpx.HTTPError as err:
            _LOGGER.debug("weReci cooking picture did not load: %s", err)
            return None
        if len(res.content) > _MAX_BYTES:
            return None
        kind = res.headers.get("content-type") or infer_image_type(res.content)
        try:
            kind = valid_image_content_type(kind)
        except ImageContentTypeError:
            return None
        # Somebody else's SVG is a document, not a picture: it can carry script.
        if "svg" in kind.lower():
            return None
        return Image(content_type=kind, content=res.content)
