"""Cooking with no phone: Home Assistant plays the phone's part on the relay.

The display side (cook_display.py) is unchanged — it still opens the channel,
shows the receiver, reads snapshots for the sensor and posts the buttons'
commands. This claims the channel's code the way a phone would, pushes the
snapshots, and answers the commands: taps on the screen, the step buttons and
voice all arrive here as intent.

It cooks the recipe as written. Scaling, Break it down and swaps run in the
weReci app, so the snapshot offers no controls for them and the screen hides
them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from typing import Any

import httpx

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client

from .const import CAST_API_PATH, CAST_POLL_TIMEOUT, CAST_REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# The receiver paints a "lost the phone" band after 60s of silence; an
# unchanged snapshot re-pushed inside that window is its pulse.
_HEARTBEAT_SECONDS = 20
_RETRY_SECONDS = 2
_PROTOCOL_VERSION = 1


def _lines(value: Any) -> list[str]:
    return [str(v).strip() for v in value or [] if str(v).strip()]


class HaSender:
    """One phone-free cook: the recipe, where we are in it, what is ticked."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        recipe: dict[str, Any],
        on_lost: Callable[[], None],
    ) -> None:
        """Initialize."""
        self.hass = hass
        self._api = f"{base_url}{CAST_API_PATH}"
        self._title = str(recipe.get("recipe_title") or recipe.get("title") or "Recipe")
        self._photo = recipe.get("primary_photo_url") or None
        self._lang = str(recipe.get("source_language") or "en")
        self._steps = _lines(recipe.get("instructions"))
        self._ingredients = _lines(recipe.get("ingredients"))
        self._step = 0
        self._checked: set[int] = set()
        self._token: str | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._on_lost = on_lost

    @property
    def has_steps(self) -> bool:
        """A recipe with no instructions has nothing to cook through."""
        return bool(self._steps)

    def snapshot(self) -> dict[str, Any]:
        """The same shape the app's Cook Mode projects."""
        total = len(self._steps)
        ingredients = [
            {"text": text, "checked": i in self._checked, "i": i}
            for i, text in enumerate(self._ingredients)
        ]
        return {
            "v": _PROTOCOL_VERSION,
            "title": self._title,
            "photoUrl": self._photo,
            "stepIdx": self._step,
            "totalSteps": total,
            "stepText": self._steps[self._step],
            "componentLabel": None,
            "scaleLabel": None,
            # Which ingredients a step introduces is app-side data.
            "newIngredients": [],
            "allIngredients": ingredients,
            "dir": "rtl" if self._lang[:2] in ("ar", "he", "fa", "ur") else "ltr",
            "lang": self._lang,
            "palette": None,
            "labels": {
                "stepOf": f"Step {self._step + 1} of {total}",
                "ready": f"{len(self._checked)} of {len(ingredients)} ready",
                "ingredients": "Ingredients",
            },
        }

    def apply(self, cmd: Any) -> bool:
        """Intent → state. True when the screen needs a new snapshot."""
        if not isinstance(cmd, dict):
            return False
        if cmd.get("do") == "step" and isinstance(cmd.get("delta"), int):
            step = min(max(self._step + cmd["delta"], 0), len(self._steps) - 1)
            changed, self._step = step != self._step, step
            return changed
        if cmd.get("do") == "toggle" and isinstance(cmd.get("i"), int):
            if 0 <= cmd["i"] < len(self._ingredients):
                self._checked ^= {cmd["i"]}
                return True
        return False  # scale / breakdown / swaps: not offered, so not answered

    async def async_start(self, code: str, create_task: Callable[..., Any]) -> None:
        """Claim the code as a phone would, and show the first step."""
        try:
            res = await get_async_client(self.hass).post(
                f"{self._api}/pair", json={"code": code}, timeout=CAST_REQUEST_TIMEOUT
            )
            res.raise_for_status()
            token = res.json().get("token")
        except (httpx.HTTPError, ValueError) as err:
            raise HomeAssistantError("weReci could not start the cook") from err
        if not isinstance(token, str):
            raise HomeAssistantError("weReci could not start the cook")
        self._token = token
        await self._push()
        self._tasks = [create_task(self._listen()), create_task(self._heartbeat())]

    async def _push(self) -> bool:
        """False once the channel is gone."""
        try:
            res = await get_async_client(self.hass).post(
                f"{self._api}/state",
                json={"token": self._token, "state": json.dumps(self.snapshot())},
                timeout=CAST_REQUEST_TIMEOUT,
            )
        except httpx.HTTPError:
            return True  # a blip; the heartbeat pushes again
        return res.status_code != 404

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(_HEARTBEAT_SECONDS)
            if not await self._push():
                self._on_lost()
                return

    async def _listen(self) -> None:
        client = get_async_client(self.hass)
        since = 0
        while True:
            try:
                res = await client.get(
                    f"{self._api}/command",
                    params={"token": self._token, "since": since},
                    timeout=CAST_POLL_TIMEOUT,
                )
            except httpx.HTTPError:
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            if res.status_code == 404:
                self._on_lost()
                return
            try:
                data = res.json() if res.status_code == 200 else None
            except ValueError:
                data = None
            if not isinstance(data, dict):
                await asyncio.sleep(_RETRY_SECONDS)
                continue
            changed = False
            for entry in data.get("commands") or []:
                if isinstance(entry, dict) and isinstance(entry.get("v"), int):
                    since = max(since, entry["v"])
                    changed = self.apply(entry.get("cmd")) or changed
            if isinstance(data.get("version"), int):
                since = max(since, data["version"])
            if changed:
                await self._push()

    def stop(self) -> None:
        """Stop answering. Closing the channel is the display side's job."""
        for task in self._tasks:
            task.cancel()
        self._tasks = []
