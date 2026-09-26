"""Cooking with no phone: Home Assistant plays the phone's part on the relay.

The display side (cook_display.py) is unchanged — it still opens the channel,
shows the receiver, reads snapshots for the sensor and posts the buttons'
commands. This claims the channel's code the way a phone would, pushes the
snapshots, and answers the commands: taps on the screen, the step buttons and
voice all arrive here as intent.

Scaling and ingredient swaps are weReci's own Cook Mode routes, called
directly with the account's token (behind the cook:assist permission) — the
same runs, metered the same way, as the taps in the app. Break it down is only ever READ:
generating one saves it onto the recipe, and a connection is promised it will
never change the collection. A recipe already broken down in the app offers
the toggle; any other recipe simply doesn't.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.httpx_client import get_async_client

from .const import (
    CAST_API_PATH,
    CAST_POLL_TIMEOUT,
    CAST_REQUEST_TIMEOUT,
    SCALE_API_PATH,
    SUBSTITUTE_API_PATH,
)

_LOGGER = logging.getLogger(__name__)

# The receiver paints a "lost the phone" band after 60s of silence; an
# unchanged snapshot re-pushed inside that window is its pulse.
_HEARTBEAT_SECONDS = 20
_RETRY_SECONDS = 2
_PROTOCOL_VERSION = 1
# A queued scale run is a durable job; the app gives up on the same order.
_SCALE_POLL_SECONDS = 2
_SCALE_POLLS = 150
_CONFIDENCE = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence"}

# (method, path, body) → the JSON answer; errors come back as {"error": …}.
Requester = Callable[[str, str, dict[str, Any] | None], Any]


class PermissionNeeded(Exception):
    """The connection was never granted cook:assist."""


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
        request: Requester | None = None,
        on_permission_needed: Callable[[], None] | None = None,
    ) -> None:
        """Initialize."""
        self.hass = hass
        self._api = f"{base_url}{CAST_API_PATH}"
        self._recipe_id = str(recipe.get("id") or "")
        self._title = str(recipe.get("recipe_title") or recipe.get("title") or "Recipe")
        self._photo = recipe.get("primary_photo_url") or None
        self._lang = str(recipe.get("source_language") or "en")
        self._written = _lines(recipe.get("instructions"))
        # A breakdown made in the app rides along on the recipe. Read, never made.
        self._broken_down = _lines(recipe.get("reformulated_instructions"))
        self._breakdown = False
        self._ingredients = _lines(recipe.get("ingredients"))
        self._step = 0
        self._checked: set[int] = set()
        self._factor = 1.0
        self._scaled: list[str] | None = None
        self._subs: dict[int, dict[str, Any]] = {}
        self._swap: dict[str, Any] | None = None
        self._busy: str | None = None
        self._request = request
        self._assist = request is not None and bool(self._recipe_id)
        self._on_permission_needed = on_permission_needed
        self._op: asyncio.Task[None] | None = None
        self._create_task: Callable[..., Any] | None = None
        self._token: str | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._on_lost = on_lost

    @property
    def _steps(self) -> list[str]:
        return self._broken_down if self._breakdown else self._written

    def _line(self, i: int) -> str:
        """What ingredient `i` reads as: scaled, else swapped, else as written."""
        if self._scaled is not None and i < len(self._scaled):
            return self._scaled[i]
        sub = self._subs.get(i)
        return str(sub["replacement"]) if sub else self._ingredients[i]

    @property
    def has_steps(self) -> bool:
        """A recipe with no instructions has nothing to cook through."""
        return bool(self._written)

    def snapshot(self) -> dict[str, Any]:
        """The same shape the app's Cook Mode projects."""
        total = len(self._steps)
        ingredients = [
            {"text": self._line(i), "checked": i in self._checked, "i": i}
            for i in range(len(self._ingredients))
        ]
        return {
            "v": _PROTOCOL_VERSION,
            "title": self._title,
            "photoUrl": self._photo,
            "stepIdx": self._step,
            "totalSteps": total,
            "stepText": self._steps[self._step],
            "componentLabel": None,
            "scaleLabel": f"{self._factor:g}×" if self._scaled is not None else None,
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
                "scaleTitle": "Scale this recipe",
                "breakdown": "🧠 Break it down",
                "breakdownOn": "🧠 Broken down",
                "thinking": "Thinking…",
            },
            "scaleFactor": self._factor if self._scaled is not None else 1,
            "breakdownActive": self._breakdown,
            "busyLabel": self._busy,
            "swapPanel": (
                {k: v for k, v in self._swap.items() if k != "_found"}
                if self._swap
                else None
            ),
            # Only what this cook can really do; the screen hides the rest.
            "controls": {
                "scale": self._assist,
                "swap": self._assist,
                "breakdown": bool(self._broken_down),
            },
        }

    def apply(self, cmd: Any) -> bool:
        """Intent → state. True when the screen needs a new snapshot."""
        if not isinstance(cmd, dict):
            return False
        do = cmd.get("do")
        if do == "step" and isinstance(cmd.get("delta"), int):
            step = min(max(self._step + cmd["delta"], 0), len(self._steps) - 1)
            changed, self._step = step != self._step, step
            return changed
        i = cmd.get("i")
        known = isinstance(i, int) and 0 <= i < len(self._ingredients)
        if do == "toggle" and known:
            self._checked ^= {i}
            return True
        if do == "breakdown" and self._broken_down:
            # Same place in the cook, on the other set of steps.
            before = max(len(self._steps) - 1, 1)
            self._breakdown = not self._breakdown
            self._step = round(self._step * (len(self._steps) - 1) / before)
            return True
        if not self._assist:
            return False  # not offered, so not answered
        if do == "scale" and isinstance(cmd.get("factor"), (int, float)):
            factor = float(cmd["factor"])
            if not 0.1 <= factor <= 10:
                return False
            self._factor = factor
            if factor == 1:
                self._scaled = None
                self._cancel_op()
                self._busy = None
                return True
            return self._begin(self._run_scale(), "Scaling…")
        if do == "swapOpts" and known:
            self._swap = self._panel(i, "pending", [])
            return self._begin(self._run_swap(i), None)
        if do == "swapPick" and known and isinstance(cmd.get("k"), int):
            found = (self._swap or {}).get("_found") or []
            if self._swap and self._swap["i"] == i and 0 <= cmd["k"] < len(found):
                self._subs[i] = found[cmd["k"]]
                self._swap = None
                return self._rescale()
            return False
        if do == "swapClear" and known:
            self._subs.pop(i, None)
            self._swap = None
            return self._rescale()
        if do == "swapCancel":
            self._swap = None
            self._cancel_op()
            return True
        return False

    # ── weReci's own scaling and swaps, over the tools ─────────────────────

    def _panel(self, i: int, status: str, found: list[dict[str, Any]]) -> dict[str, Any]:
        line = self._ingredients[i]
        return {
            "i": i,
            "title": f"Swap for {line[:40]}{'…' if len(line) > 40 else ''}",
            "status": status,
            "options": [
                {
                    "replacement": str(f.get("replacement") or ""),
                    "ratioText": f"Ratio: {f.get('ratio') or '1:1'}",
                    "confidenceText": _CONFIDENCE.get(str(f.get("confidence")), ""),
                    "reasoning": f.get("reasoning"),
                    "caveats": f.get("caveats"),
                }
                for f in found
            ],
            "hasSubstitution": i in self._subs,
            "restoreLabel": "Restore original",
            "errorText": "Couldn't get suggestions. Try again.",
            "emptyText": "No good substitute found.",
            "_found": found,
        }

    def _cancel_op(self) -> None:
        if self._op is not None:
            self._op.cancel()
            self._op = None

    def _begin(self, coro: Any, busy: str | None) -> bool:
        """One long run at a time; the newest ask wins."""
        self._cancel_op()
        self._busy = busy
        if self._create_task is None:
            coro.close()
        else:
            self._op = self._create_task(coro)
        return True

    def _rescale(self) -> bool:
        """A swap changed under a scaled recipe: the scaled lines are stale."""
        if self._scaled is None:
            return True
        return self._begin(self._run_scale(), "Scaling…")

    async def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        assert self._request is not None
        got = await self._request(method, path, body)
        if got.get("error") == "permission_required":
            raise PermissionNeeded
        return got

    async def _run_scale(self) -> None:
        try:
            got = await self._call(
                "POST",
                SCALE_API_PATH,
                {
                    "recipeId": self._recipe_id,
                    "factor": self._factor,
                    "notes": "",
                    "substitutions": {
                        str(i): {"replacement": str(s.get("replacement") or "")}
                        for i, s in self._subs.items()
                    },
                },
            )
            # Queued as a durable run: poll it, as the app does.
            job_id, polls = got.get("job_id"), 0
            while job_id and (
                got.get("status") == "pending" or got.get("state") == "running"
            ):
                polls += 1
                if polls > _SCALE_POLLS:
                    raise HomeAssistantError("scaling timed out")
                await asyncio.sleep(_SCALE_POLL_SECONDS)
                got = await self._call("GET", f"{SCALE_API_PATH}?job_id={quote(str(job_id))}")
            if got.get("error") or got.get("state") == "failed":
                raise HomeAssistantError(f"scaling failed: {got.get('error')}")
            result = got.get("result") if got.get("state") == "done" else got
            lines = _lines((result or {}).get("scaledIngredients"))
            if len(lines) != len(self._ingredients):
                raise HomeAssistantError("scaling failed")
            self._scaled = lines
        except PermissionNeeded:
            self._lose_assist()
        except HomeAssistantError as err:
            _LOGGER.warning("weReci cook display: %s", err)
            self._factor, self._scaled = 1.0, None
        self._busy = None
        self._op = None
        await self._push()

    async def _run_swap(self, i: int) -> None:
        try:
            got = await self._call(
                "POST",
                SUBSTITUTE_API_PATH,
                {"recipeId": self._recipe_id, "missingIngredient": self._ingredients[i]},
            )
            found = [f for f in got.get("suggestions") or [] if isinstance(f, dict)]
            status = "error" if got.get("error") else "ready"
            self._swap = self._panel(i, status, found)
        except PermissionNeeded:
            self._lose_assist()
        except HomeAssistantError:
            self._swap = self._panel(i, "error", [])
        self._op = None
        await self._push()

    def _lose_assist(self) -> None:
        """No cook:assist on this connection: stop offering, ask to reconnect."""
        self._assist, self._swap = False, None
        self._factor, self._scaled = 1.0, None
        if self._on_permission_needed:
            self._on_permission_needed()

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
        self._create_task = create_task
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
        self._cancel_op()
        for task in self._tasks:
            task.cancel()
        self._tasks = []
