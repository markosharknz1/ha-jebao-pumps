"""How pumps get named in Home Assistant's UI.

Its own module because both entity.py (device name) and config_flow.py
(config entry title) need it, and config_flow should not have to import
the entity/coordinator layer just to format a string.
"""
from __future__ import annotations

import re

# "Local Wavemaker (WiFi+BLE)" -> "Local Wavemaker". The parenthetical is
# connectivity, not identity, and the device row already prints the full
# product name as the model directly underneath the name.
_CONNECTIVITY_SUFFIX = re.compile(r"\s*\([^()]*\)\s*$")


def default_device_name(name_en: str, mac: str | None, did: str) -> str:
    """A name that isn't just a repeat of the model.

    The device name and model were both `schema.name_en`, so HA's device
    list read "Local Wavemaker (WiFi+BLE)" with "Local Wavemaker
    (WiFi+BLE)" as its own subtitle, and two identical pumps differed
    only by HA appending "2". Naming them "Local Wavemaker a4d4" keeps
    the model line informative and makes identical units tellable apart
    by the same MAC tail shown on the device page and in the discovery
    picker.

    Used for the config entry title too, so the integration page's
    "entry > device" nesting doesn't show the product name twice over.

    Only a default: a name the user sets is stored separately by HA
    (`name_by_user` for devices, the entry title for entries) and is not
    affected by this.
    """
    base = _CONNECTIVITY_SUFFIX.sub("", name_en).strip() or name_en
    # MAC first - it's what a router's client list shows. did is a stable
    # fallback for entries whose MAC hasn't been backfilled yet.
    suffix = (mac or did or "").strip().lower()[-4:]
    return f"{base} {suffix}" if suffix else base
