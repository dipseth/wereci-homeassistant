"""Constants for the weReci integration."""

from datetime import timedelta

DOMAIN = "wereci"

DEFAULT_BASE_URL = "https://wereci.xyz"
MCP_PATH = "/api/mcp"
OAUTH_DISCOVERY_PATH = "/.well-known/oauth-authorization-server"
MY_REDIRECT_URI = "https://my.home-assistant.io/redirect/oauth"

# recipes:read is the base grant; list:sync is the synced shopping list.
SCOPES = "recipes:read list:sync"
CLIENT_NAME = "Home Assistant"

CONF_BASE_URL = "base_url"
CONF_CLIENT_ID = "client_id"
CONF_AUTHORIZE_URL = "authorize_url"
CONF_TOKEN_URL = "token_url"

# A seq check is one Redis HGET server-side; the full list is only re-read
# when the seq moved.
LIST_POLL_INTERVAL = timedelta(seconds=30)
TOOLS_POLL_INTERVAL = timedelta(minutes=30)
TOOL_TIMEOUT = 45
LIST_TIMEOUT = 20

TOOL_GET_LIST = "get_shopping_list"
TOOL_UPDATE_LIST = "update_shopping_list"
# The todo entity owns these two; the Assist agent gets everything else plus
# them (ticking a line by voice is the point).
