"""Tests for Karofi utility and normalization functions."""

from custom_components.karofi.const import (
    GattProperty,
    KarofiDevicePrefix,
    KarofiShortUUID,
)
from custom_components.karofi.util import (
    hide_mac,
    is_karofi_local_name,
    is_valid_mac,
    local_names_match,
    mac_addresses_match,
    normalize_local_name,
    normalize_mac,
)


def test_normalize_mac_various_formats() -> None:
    """Test normalize_mac with different casing, delimiters, and whitespace."""
    # Standard colon-separated
    assert normalize_mac("aa:bb:cc:11:22:33") == "AA:BB:CC:11:22:33"
    assert normalize_mac("AA:BB:CC:11:22:33", uppercase=False) == "aa:bb:cc:11:22:33"

    # Hyphen-separated
    assert normalize_mac("aa-bb-cc-11-22-33") == "AA:BB:CC:11:22:33"
    assert normalize_mac("AA-BB-CC-11-22-33", uppercase=False) == "aa:bb:cc:11:22:33"

    # No delimiters
    assert normalize_mac("aabbcc112233") == "AA:BB:CC:11:22:33"
    assert normalize_mac("AABBCC112233", uppercase=False) == "aa:bb:cc:11:22:33"

    # Whitespace padded
    assert normalize_mac("   aa:bb:cc:11:22:33   ") == "AA:BB:CC:11:22:33"

    # Malformed length fallback
    assert normalize_mac("invalid") == "INVALID"
    assert normalize_mac("invalid", uppercase=False) == "invalid"


def test_mac_addresses_match() -> None:
    """Test mac_addresses_match across case and delimiter variations."""
    assert mac_addresses_match("aa:bb:cc:11:22:33", "AA:BB:CC:11:22:33")
    assert mac_addresses_match("AA-BB-CC-11-22-33", "aa:bb:cc:11:22:33")
    assert mac_addresses_match("aabbcc112233", "AA:BB:CC:11:22:33")
    assert mac_addresses_match("  aa-bb-cc-11-22-33  ", "AABBCC112233")

    # Mismatched addresses
    assert not mac_addresses_match("aa:bb:cc:11:22:33", "AA:BB:CC:DD:EE:FF")

    # Empty inputs
    assert not mac_addresses_match("", "")
    assert not mac_addresses_match("aa:bb:cc:11:22:33", "")


def test_is_valid_mac() -> None:
    """Test is_valid_mac with valid and invalid addresses."""
    assert is_valid_mac("AA:BB:CC:11:22:33")
    assert is_valid_mac("AA-BB-CC-11-22-33")
    assert is_valid_mac("aabbcc112233")
    assert is_valid_mac("  aa:bb:cc:11:22:33  ")

    assert not is_valid_mac("AA:BB:CC:11:22:3G")  # Invalid hex char
    assert not is_valid_mac("AA:BB:CC:11:22")  # Incomplete length
    assert not is_valid_mac("AA:BB:CC:11:22:33:11")  # Too long
    assert not is_valid_mac("")


def test_normalize_local_name() -> None:
    """Test normalize_local_name handling None, whitespace, nulls, and casing."""
    assert normalize_local_name(None) == ""
    assert normalize_local_name("") == ""
    assert normalize_local_name("   ") == ""

    # Case-insensitive by default
    assert normalize_local_name("  TCM-41A00000068\x00 ") == "tcm-41a00000068"

    # Case-sensitive when specified
    assert normalize_local_name("  TCM-41A00000068\x00 ", case_sensitive=True) == "TCM-41A00000068"


def test_local_names_match() -> None:
    """Test local_names_match with case sensitive and insensitive comparisons."""
    assert local_names_match("TCM-41A00000068", "tcm-41a00000068")
    assert local_names_match("  Karofi Purifier  ", "karofi purifier")

    assert not local_names_match(
        "TCM-41A00000068",
        "tcm-41a00000068",
        case_sensitive=True,
    )
    assert not local_names_match("TCM-01", "Karofi-01")
    assert not local_names_match(None, "TCM-01")
    assert not local_names_match("", "")


def test_is_karofi_local_name() -> None:
    """Test is_karofi_local_name against supported Karofi prefix."""
    assert is_karofi_local_name("TCM-41A00000068")
    assert is_karofi_local_name("tcm-41a00000068")
    assert not is_karofi_local_name("Karofi Water Purifier")
    assert not is_karofi_local_name("karofi-smart")

    assert not is_karofi_local_name("SmartPurifier")
    assert not is_karofi_local_name("Xiaomi-Purifier")
    assert not is_karofi_local_name("K365-Pro-01")
    assert not is_karofi_local_name("")
    assert not is_karofi_local_name(None)


def test_constants_and_enums() -> None:
    """Test that newly added enums hold expected literal values."""
    assert KarofiShortUUID.SERVICE == "12ac"
    assert KarofiShortUUID.TDS_SENSOR == "12ac"
    assert KarofiShortUUID.COMMAND == "12ab"
    assert KarofiShortUUID.WIFI_PROV_RX == "ab01"
    assert KarofiShortUUID.WIFI_STATUS_TX == "ab02"
    assert KarofiShortUUID.OTA_FIRMWARE == "ac02"
    assert KarofiShortUUID.CCCD == "2902"

    assert GattProperty.READ == "read"
    assert GattProperty.NOTIFY == "notify"
    assert GattProperty.WRITE == "write"
    assert GattProperty.INDICATE == "indicate"

    assert KarofiDevicePrefix.TCM == "tcm"


def test_hide_mac() -> None:
    """Test hide_mac helper across formats, delimiters, and edge cases."""
    # Standard colon-separated
    assert hide_mac("aa:bb:cc:11:22:33") == "**:**:**:**:22:33"
    assert hide_mac("AA:BB:CC:11:22:33") == "**:**:**:**:22:33"

    # Hyphen-separated
    assert hide_mac("aa-bb-cc-11-22-33") == "**:**:**:**:22:33"
    assert hide_mac("AA-BB-CC-11-22-33") == "**:**:**:**:22:33"

    # No delimiters
    assert hide_mac("aabbcc112233") == "**:**:**:**:22:33"
    assert hide_mac("AABBCC112233") == "**:**:**:**:22:33"

    # Whitespace padded
    assert hide_mac("   aa:bb:cc:11:22:33   ") == "**:**:**:**:22:33"

    # Already masked
    assert hide_mac("**:**:**:**:22:33") == "**:**:**:**:22:33"

    # Empty and None
    assert hide_mac("") == ""
    assert hide_mac(None) == ""
    assert hide_mac("   ") == ""

    # Short and fallback strings
    assert hide_mac("invalid_long") == "**:_LONG"
    assert hide_mac("abc") == "****"
    assert hide_mac("a:b:c:d:e:f") == "**:D:E:F"
