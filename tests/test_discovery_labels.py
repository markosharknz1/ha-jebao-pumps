"""The discovery picker used to list raw Gizwits cloud `did`s
("DBaDWkpGq20NUtEw8ysPRw (10.42.1.88, product_key=50dbc922...)"), which
tell a person nothing - especially with several identical pumps on one
network. It now shows what each device actually is, using the bundled
schema's English product name.

Needs the real `homeassistant` package (config_flow imports it at module
level) - skips cleanly if absent, same convention as the other tests here.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local.config_flow import (  # noqa: E402
    _product_names,
    discovery_label,
)
from custom_components.jebao_local.jebao_gizwits.discovery import DiscoveredDevice  # noqa: E402

WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"
# A product_key with no bundled schema. (50dbc922... used to be the real
# example here, until Phase 19 captured its schema and it became supported -
# so this is a deliberately synthetic key that can never become real.)
UNKNOWN_PRODUCT_KEY = "00000000000000000000000000000000"


def _device(ip: str, did: str, mac_hex: str, product_key: str) -> DiscoveredDevice:
    return DiscoveredDevice(
        ip=ip, did=did, mac_hex=mac_hex, wifi_firmware="",
        product_key=product_key, api_server="", version="", extra={},
    )


def test_label_names_the_product_not_the_cloud_id():
    device = _device("10.42.1.82", "UiFhBnPD7gQBDBF17ZBF27", "aabbccdd9e01", WAVEMAKER_PRODUCT_KEY)
    label = discovery_label(device, _product_names({WAVEMAKER_PRODUCT_KEY})[WAVEMAKER_PRODUCT_KEY])
    assert "Local Wavemaker" in label
    assert "10.42.1.82" in label
    assert device.did not in label  # the whole point - no opaque cloud id


def test_label_includes_mac_tail_to_tell_identical_models_apart():
    a = _device("10.42.1.88", "did-a", "aabbccdd1f4a", WAVEMAKER_PRODUCT_KEY)
    b = _device("10.42.1.89", "did-b", "aabbccdd2b7c", WAVEMAKER_PRODUCT_KEY)
    name = _product_names({WAVEMAKER_PRODUCT_KEY})[WAVEMAKER_PRODUCT_KEY]
    label_a, label_b = discovery_label(a, name), discovery_label(b, name)
    assert label_a != label_b
    assert "1f4a" in label_a and "2b7c" in label_b


def test_unsupported_product_is_labelled_as_such_rather_than_blank():
    device = _device("10.42.1.87", "did-c", "aabbccdd33f5", UNKNOWN_PRODUCT_KEY)
    label = discovery_label(device, _product_names({UNKNOWN_PRODUCT_KEY})[UNKNOWN_PRODUCT_KEY])
    assert "Unsupported" in label
    assert UNKNOWN_PRODUCT_KEY[:8] in label  # enough to report/look up
    assert "10.42.1.87" in label


def test_product_names_returns_none_for_unknown_key_without_raising():
    names = _product_names({WAVEMAKER_PRODUCT_KEY, UNKNOWN_PRODUCT_KEY})
    assert names[WAVEMAKER_PRODUCT_KEY]
    assert names[UNKNOWN_PRODUCT_KEY] is None


def test_label_survives_a_device_with_no_mac():
    device = _device("10.42.1.90", "did-d", "", WAVEMAKER_PRODUCT_KEY)
    label = discovery_label(device, _product_names({WAVEMAKER_PRODUCT_KEY})[WAVEMAKER_PRODUCT_KEY])
    assert "10.42.1.90" in label  # no crash, still identifiable by IP
