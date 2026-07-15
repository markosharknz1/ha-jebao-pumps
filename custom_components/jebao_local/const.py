"""Constants for the Jebao Local integration."""

DOMAIN = "jebao_local"

CONF_DID = "did"
CONF_PRODUCT_KEY = "product_key"
CONF_MAC = "mac"

DEFAULT_SCAN_INTERVAL = 10  # seconds - LAN polling is cheap, no cloud rate limits
MIN_SCAN_INTERVAL = 5
MAX_SCAN_INTERVAL = 120

DISCOVERY_TIMEOUT = 5.0
BACKGROUND_DISCOVERY_INTERVAL = 300  # seconds - passive scan for new/unconfigured pumps

TCP_PORT = 12416
