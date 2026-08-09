"""Constants for the Jebao Local integration."""

DOMAIN = "jebao_local"

CONF_DID = "did"
CONF_PRODUCT_KEY = "product_key"
CONF_MAC = "mac"

DEFAULT_SCAN_INTERVAL = 10  # seconds - LAN polling is cheap, no cloud rate limits
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 120

DISCOVERY_TIMEOUT = 5.0
# Ceiling on one connect+authenticate+read cycle. Home Assistant puts no
# timeout of its own around a coordinator update, so without this a read
# on a half-open socket (the peer vanished without sending a RST) blocks
# forever and that pump simply stops updating until HA restarts.
SESSION_TIMEOUT = 10.0
BACKGROUND_DISCOVERY_INTERVAL = 300  # seconds - passive scan for new/unconfigured pumps

TCP_PORT = 12416
