"""The cooking picture: what a picture-glance card is fed."""

import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

from homeassistant.core import HomeAssistant

from custom_components.wereci.image import draw_card, safe_photo_url

from .test_cook_display import (  # noqa: F401 — `relay` is a fixture
    IMAGE,
    SNAPSHOT,
    _cast_player,
    _Res,
    _setup,
    _show,
    relay,
)

PHOTO = "https://wereci.xyz/api/fairbanks-recipes/asset/soup.webp"
JPEG = b"\xff\xd8\xff\xe0 soup"


class _Photos:
    """Stands in for the entity's own httpx client."""

    def __init__(self, answers: dict[str, httpx.Response]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def get(self, url: str, **_) -> httpx.Response:
        self.asked.append(url)
        res = self.answers[url]
        res.request = httpx.Request("GET", url)
        return res


async def _push(hass: HomeAssistant, relay, version: int, **snapshot) -> None:
    state = json.dumps({**SNAPSHOT, **snapshot})
    relay.polls.put_nowait(_Res(200, {"version": version, "paired": True, "state": state}))
    await asyncio.sleep(0)
    await hass.async_block_till_done()


async def _get(hass: HomeAssistant, hass_client) -> tuple[str, bytes]:
    res = await (await hass_client()).get(f"/api/image_proxy/{IMAGE}")
    assert res.status == 200
    return res.headers["Content-Type"], await res.read()


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (PHOTO, True),
        ("/api/fairbanks-recipes/asset/soup.webp", True),  # relative: the account's site
        ("https://upload.wikimedia.org/x.jpg", True),
        ("http://wereci.xyz/x.jpg", False),
        ("https://192.168.1.10/x.jpg", False),
        ("https://[::1]/x.jpg", False),
        ("https://homeassistant.local/x.jpg", False),
        ("https://nas/x.jpg", False),
        ("data:image/svg+xml,<svg/>", False),
        (None, False),
    ],
)
def test_only_public_https_photos_are_fetched(url, ok) -> None:
    got = safe_photo_url("https://wereci.xyz", url)
    assert (got is not None) == ok
    if url and url.startswith("/"):
        assert got == PHOTO


def test_a_card_escapes_what_it_is_given() -> None:
    card = draw_card("<script>alert(1)</script> & soup").decode()
    assert "<script>" not in card and "&amp; soup" in card


async def test_the_picture_follows_the_cook_not_the_step(
    hass: HomeAssistant, entry, list_call, relay, hass_client
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    kind, idle = await _get(hass, hass_client)
    assert kind == "image/svg+xml" and b"Nothing is cooking" in idle
    stamp = hass.states.get(IMAGE).state

    # Waiting: the code is in the picture.
    await _show(hass, entity_id=_cast_player(hass), notify=[])
    assert (waiting := hass.states.get(IMAGE).state) != stamp
    assert b"ABCDEF" in (await _get(hass, hass_client))[1]

    # Cooking: the recipe's own photo.
    photos = _Photos({PHOTO: httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})})
    hass.data["entity_components"]["image"].get_entity(IMAGE)._client = photos
    await _push(hass, relay, 1, photoUrl=PHOTO)
    assert (cooking := hass.states.get(IMAGE).state) != waiting
    assert await _get(hass, hass_client) == ("image/jpeg", JPEG)

    # A step is the same picture: no new stamp, no second fetch.
    await _push(hass, relay, 2, photoUrl=PHOTO, stepIdx=2)
    assert hass.states.get(IMAGE).state == cooking
    assert (await _get(hass, hass_client))[1] == JPEG
    assert photos.asked == [PHOTO]


async def test_no_photo_or_a_bad_one_shows_the_title(
    hass: HomeAssistant, entry, list_call, relay, hass_client
) -> None:
    await _setup(hass, entry)
    async_mock_service(hass, "cast", "show_lovelace_view")
    await _show(hass, entity_id=_cast_player(hass), notify=[])
    await _push(hass, relay, 1)
    assert b"Carrot soup" in (await _get(hass, hass_client))[1]

    # A redirect off to somewhere private is not followed.
    photos = _Photos(
        {PHOTO: httpx.Response(302, headers={"location": "https://10.0.0.2/admin"})}
    )
    hass.data["entity_components"]["image"].get_entity(IMAGE)._client = photos
    await _push(hass, relay, 2, photoUrl=PHOTO)
    kind, body = await _get(hass, hass_client)
    assert kind == "image/svg+xml" and b"Carrot soup" in body
    assert photos.asked == [PHOTO]
