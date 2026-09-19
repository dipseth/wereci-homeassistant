<p align="center">
  <a href="https://wereci.xyz">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/brand/wereci-logo-on-dark.png">
      <img src="docs/brand/wereci-logo.png" alt="weReci" width="420">
    </picture>
  </a>
</p>

<h3 align="center">weReci for Home Assistant</h3>

<p align="center">
  Your recipes, beautifully kept — now in your kitchen's Home Assistant.
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=dipseth&repository=wereci-homeassistant&category=integration"><img src="https://img.shields.io/badge/HACS-add%20repository-0B3B24?style=for-the-badge&logo=homeassistantcommunitystore&logoColor=white" alt="Add to HACS"></a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=wereci"><img src="https://img.shields.io/badge/Home%20Assistant-add%20integration-5F9D8A?style=for-the-badge&logo=homeassistant&logoColor=white" alt="Add integration"></a>
  <a href="https://wereci.xyz"><img src="https://img.shields.io/badge/weReci-wereci.xyz-D6917B?style=for-the-badge" alt="wereci.xyz"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/github/v/release/dipseth/wereci-homeassistant?color=9C8A45&label=release&style=flat-square" alt="release">
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.9%2B-0B3B24?style=flat-square" alt="Home Assistant 2026.9+">
  <img src="https://img.shields.io/badge/auth-OAuth%202.1%20%2B%20PKCE-5F9D8A?style=flat-square" alt="OAuth 2.1 + PKCE">
</p>

---

Connect your [weReci](https://wereci.xyz) cookbook to Home Assistant. One sign-in gives you:

- 🍳 **Recipe tools for Assist** — ask your voice or chat assistant what you can cook, look up a recipe, or put a recipe's ingredients on your shopping list.
- 🛒 **Your shopping list as a to-do list** — the list you share in weReci (List It) shows up as a `todo` entity. Tick, add, rename and remove lines from a dashboard or by voice; changes show up on your phones, and the other way round.
- 📺 **Cook Mode on a kitchen screen** — one service call puts weReci's cook display on a Nest Hub, wall tablet, Android TV or Fire TV and sends the pairing link to your phone. Your phone drives it; Home Assistant adds next/previous-step buttons and a cooking sensor.

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
3. For Assist: Settings → Voice assistants → your assistant → **Conversation agent** → the ⚙️ gear next to it → under **Control Home Assistant**, tick **weReci (your email)**. Leave **Assist** ticked too, so the agent can still control your home.

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

## Cook Mode on a screen

`wereci.show_cook_display` opens a cook display, puts it on the screen you name, and notifies your phone with a link. Tap the link, open any recipe's Cook Mode, and the screen follows your phone — scaling, swaps and all. The 6-letter code on the screen works too if the link never arrives (it is good for 10 minutes).

```yaml
action: wereci.show_cook_display
data:
  entity_id: media_player.kitchen_display   # Cast, Android TV or Fire TV
  # browser_id: kitchen-tablet              # …or a browser_mod browser instead
  # notify: [notify.mobile_app_my_phone]    # default: every mobile app
```

**Start on a recipe.** Add `recipe` (a name — the closest match in your collection) or `recipe_id` (as the recipe tools return it), and the notification opens Cook Mode on that recipe directly — one tap and the screen is cooking:

```yaml
action: wereci.show_cook_display
data:
  entity_id: media_player.kitchen_display
  recipe: carrot soup
```

A name has to resemble the recipe's title. weReci's search is semantic — it always finds the *nearest* recipe, so “lasagna” would otherwise start your closest pasta. When nothing resembles the name, the action refuses and lists the closest titles; pass `allow_closest: true` to cook the nearest match anyway, or use `recipe_id`.

An assistant with the weReci tools can do the same after a search: “find me a soup, and put it on the kitchen display.”

**Cook with no phone at all.** Add `without_phone: true` and Home Assistant runs the cook itself: it fetches the recipe, puts step 1 on the screen, and answers every tap on the screen, the step buttons and voice. No notification is sent.

```yaml
action: wereci.show_cook_display
data:
  entity_id: media_player.kitchen_display
  recipe: carrot soup
  without_phone: true
```

From the screen you can also:

- **Scale** the recipe and **swap** an ingredient you're missing. These are weReci's own — the same runs as the taps in the app, counted against your AI allowance the same way — and need the `cook:assist` permission. If you connected before it existed, the first scale or swap makes Home Assistant ask you to sign in to weReci again; approve the new line and they work from then on.
- **Break it down**, when the recipe was already broken down in the weReci app. Home Assistant never *generates* a breakdown: that saves new steps onto the recipe, and this connection is never allowed to change your collection.

The "new at this step" ingredient rail is the one thing a phone-driven cook has that this doesn't.

| Target | How it is shown | Needs |
|---|---|---|
| Cast device (Nest Hub, Chromecast) | `cast.show_lovelace_view` | Home Assistant reachable over **https** (Nabu Casa or your own domain) |
| `browser_mod` browser | `browser_mod.navigate` | [browser_mod](https://github.com/thomasloven/hass-browser_mod) |
| Android TV / Fire TV (`androidtv`) | adb `VIEW` intent | nothing else |
| Android TV Remote | `media_player.play_media` (url) | nothing else |

**The weReci dashboard** appears in the sidebar by itself — there is nothing to build. Start a cook from it: type what you want under **Recipe** (words, or a recipe id) — weReci is searched in the background and **Matches** fills with what it found. Words that name one recipe pick it for you; a vague word (“pasta”) leaves the pick to you. Then pick a screen, choose whether a phone is involved, press **Start cooking**. Building your own card? `select.…_matches` carries the whole search as attributes: `query`, `searching`, `error`, `recipe_id` (the pick) and `matches` (`id`, `title`, `cuisine`, `cookbook`). Below that, its control panel shows what is cooking, has previous / next / stop buttons, the ingredient list, and a live copy of the screen that takes taps just like the screen does. The same dashboard holds the hidden full-screen view that Cast devices and browser_mod browsers are shown; it points at a local address (`display_path` on `sensor.…_cooking`) that forwards to whichever cook display is live. Treat `display_path` like a password for your kitchen screen: anyone who can reach your Home Assistant and knows it can see the recipe step being cooked, and nothing else.

Don't want it, or the to-do list? **Settings → Devices & services → weReci → the gear on the account** has a checkbox for each: *Shopping list* and *weReci control panel*. Turning the panel off hides the sidebar entry; cook displays keep working.

Rather lay it out yourself? Put a webpage card on `display_path` in any dashboard and pass `dashboard_path` / `view_path` to the service. A dashboard of your own at `/wereci-cook` is left alone.

`wereci.stop_cook_display` closes it and gives the screen back; closing Cook Mode on the phone does the same.

**Entities:** `sensor.…_cooking` is `idle` / `waiting` / `cooking`, with `title`, `step`, `total_steps` and `step_text` (and `code` + `link` while waiting — handy for an NFC tag automation). `button.…_next_step` and `button.…_previous_step` work while cooking, and `wereci.toggle_ingredient` (`index`, from the sensor's `ingredients` list) ticks one off. `driven_by` says whether a phone or Home Assistant is running the cook.

**Voice, without an LLM:** add `config/custom_sentences/en/wereci.yaml`:

```yaml
language: en
intents:
  WereciNextStep:
    data:
      - sentences: ["next step", "next recipe step"]
  WereciPreviousStep:
    data:
      - sentences: ["previous step", "go back a step"]
```

The show service also returns `{code, link}` (plus `recipe_id` and `title` when a recipe was given) when called with a response variable.

## How it connects

OAuth 2.1 with PKCE — no client secret. On first setup the integration registers your Home Assistant as a public client with weReci, then talks to weReci's MCP server (`https://wereci.xyz/api/mcp`) with the scopes `recipes:read list:sync cook:assist`. It can never add, change, or delete recipes in your collection. The cook display uses weReci's public pair-by-code relay instead: Home Assistant holds a token for one short-lived display channel and never sees your recipes — only the step your phone chooses to show.

Home Assistant's built-in Model Context Protocol integration can't be used instead: it requires a client secret and doesn't send a PKCE challenge, and weReci only accepts public PKCE clients.

## Develop

    uv sync && uv run pytest

Tests run against real Home Assistant via `pytest-homeassistant-custom-component`; `mcp` is pinned to the version Home Assistant core ships.

---

<p align="center">
  <a href="https://wereci.xyz"><img src="docs/brand/wereci-mark.svg" alt="" width="40"></a><br>
  <sub><a href="https://wereci.xyz">wereci.xyz</a> &nbsp;·&nbsp; weReci™ &nbsp;·&nbsp; Not affiliated with Home Assistant or Nabu Casa.</sub>
</p>
