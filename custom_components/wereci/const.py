"""Constants for the weReci integration."""

from datetime import timedelta

DOMAIN = "wereci"

DEFAULT_BASE_URL = "https://wereci.xyz"
MCP_PATH = "/api/mcp"
OAUTH_DISCOVERY_PATH = "/.well-known/oauth-authorization-server"
MY_REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"

# recipes:read is the base grant; list:sync is the synced shopping list;
# cook:assist is scaling and swaps on a phone-free cook display.
SCOPES = "recipes:read list:sync cook:assist"
CLIENT_NAME = "Home Assistant"

CONF_BASE_URL = "base_url"
CONF_CLIENT_ID = "client_id"
CONF_AUTHORIZE_URL = "authorize_url"
CONF_TOKEN_URL = "token_url"
CONF_DISPLAY_SECRET = "display_secret"

# Options, per account. Both default to on.
CONF_SHOPPING_LIST = "shopping_list"
CONF_CONTROL_PANEL = "control_panel"

# A seq check is one Redis HGET server-side; the full list is only re-read
# when the seq moved.
LIST_POLL_INTERVAL = timedelta(seconds=30)
TOOLS_POLL_INTERVAL = timedelta(minutes=30)
TOOL_TIMEOUT = 45
LIST_TIMEOUT = 20

TOOL_GET_LIST = "get_shopping_list"
TOOL_UPDATE_LIST = "update_shopping_list"
TOOL_GET_RECIPE = "get_recipe"
TOOL_SEARCH_RECIPES = "search_recipes"
TOOL_SCALE = "scale_recipe"
TOOL_SCALE_JOB = "get_scale_job"
TOOL_SUBSTITUTE = "suggest_substitute"
# The todo entity owns these two; the Assist agent gets everything else plus
# them (ticking a line by voice is the point).

# Cook display: the pair-by-code cast relay, with Home Assistant as the display.
CAST_API_PATH = "/api/fairbanks-recipes/cast"
CAST_RECEIVER_PATH = "/cast"
CAST_REQUEST_TIMEOUT = 12
# The relay holds a poll ~25s; clear that with margin.
CAST_POLL_TIMEOUT = 35
DEFAULT_DASHBOARD_PATH = "wereci-cook"
# The mark, served as a custom icon set (frontend/icons.js).
ICONS_URL = "/wereci_static/icons.js"
ICON_MARK = "wereci:mark"

SERVICE_SHOW_COOK_DISPLAY = "show_cook_display"
SERVICE_STOP_COOK_DISPLAY = "stop_cook_display"
SERVICE_TOGGLE_INGREDIENT = "toggle_ingredient"
INTENT_NEXT_STEP = "WereciNextStep"
INTENT_PREVIOUS_STEP = "WereciPreviousStep"
