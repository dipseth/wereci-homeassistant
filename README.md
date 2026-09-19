# weReci for Home Assistant

Connect your [weReci](https://wereci.xyz) cookbook to Home Assistant. One sign-in gives you:

- **Recipe tools for Assist** — ask your voice or chat assistant what you can cook, look up a recipe, or put a recipe's ingredients on your shopping list.
- **Your shopping list as a to-do list** — the list you share in weReci (List It) shows up as a `todo` entity. Tick, add, rename and remove lines from a dashboard or by voice; changes show up on your phones, and the other way round.

## Requirements

- Home Assistant **2026.9** or newer.
- A weReci account (guest sessions can't connect).
- Home Assistant reachable over **https**, or **My Home Assistant** enabled (it is by default). weReci won't redirect a sign-in to a plain-http address.

## Install

**HACS:** HACS → ⋮ → Custom repositories → add `https://github.com/dipseth/wereci-homeassistant` as an *Integration* → install **weReci** → restart Home Assistant.

**Manual:** copy `custom_components/wereci/` into your `/config/custom_components/` folder and restart.

## Set up

1. In the browser you use for Home Assistant, sign in at [wereci.xyz](https://wereci.xyz) with your account first.
2. Settings → Devices & services → **Add integration** → **weReci**, and approve the connection. There is no client ID or secret to enter.
3. For Assist: Settings → Voice assistants → your agent → LLM API → tick **weReci**.

The entry is named after your weReci email. You can disconnect any time from weReci → Your AI → Connected AI apps.

## The shopping list entity

Only a list weReci holds on its servers can sync — today that is the list you **share with your cookbook partner**. A list kept only on your phone stays on your phone. When there is nothing to show, the entity is `unavailable` and its `reason` attribute says why:

| `reason` | Meaning |
|---|---|
| `no_synced_list` | This account has no shared list. |
| `not_shared` | A shared list exists, but you haven't joined it — join from List It in the app. |
| `permission_required` | The shopping-list permission wasn't approved. Remove and re-add the integration. |
| `unavailable` | weReci couldn't be reached. |

Renaming a line replaces it (weReci keys a line on its ingredient). Home Assistant checks for changes every 30 seconds.

## How it connects

OAuth 2.1 with PKCE — no client secret. On first setup the integration registers your Home Assistant as a public client with weReci, then talks to weReci's MCP server (`https://wereci.xyz/api/mcp`) with the scopes `recipes:read list:sync`. It can never add, change, or delete recipes in your collection.

Home Assistant's built-in Model Context Protocol integration can't be used instead: it requires a client secret and doesn't send a PKCE challenge, and weReci only accepts public PKCE clients.

## Develop

    uv sync && uv run pytest

Tests run against real Home Assistant via `pytest-homeassistant-custom-component`; `mcp` is pinned to the version Home Assistant core ships.
