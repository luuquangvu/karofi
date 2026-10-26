"""Utility and normalization helpers for Karofi integration."""

import re
from typing import Final

from .const import FILTER_PREFIX, KarofiDevicePrefix, KarofiFilter

MAC_VALID_PATTERN: Final = re.compile(
    r"^(?:"
    r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}"
    r"|[0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5}"
    r"|[0-9A-Fa-f]{4}(?:\.[0-9A-Fa-f]{4}){2}"
    r"|[0-9A-Fa-f]{12}"
    r")$"
)
MASKED_MAC_PATTERN: Final = re.compile(r"\*\*(?::\*\*){3}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}")
MAC_SEPARATOR_TABLE: Final = str.maketrans("", "", ":-. \t\r\n\v\f")

SUPPORTED_LOCAL_NAME_PREFIXES: Final[tuple[str, ...]] = (KarofiDevicePrefix.TCM.value,)


def _canonical_mac(mac: str) -> str | None:
    """Return a canonical lowercase MAC address, or None for invalid input.

    Args:
        mac: Stripped MAC address string.

    Returns:
        Colon-separated lowercase MAC address, or None if the input does not use
        a supported MAC address format.
    """
    if not MAC_VALID_PATTERN.fullmatch(mac):
        return None
    return bytes.fromhex(mac.translate(MAC_SEPARATOR_TABLE)).hex(":")


def normalize_mac(mac: str, *, uppercase: bool = True) -> str:
    """Normalize a MAC address string to standard colon-separated notation.

    Accepts formats such as 'AA:BB:CC:11:22:33', 'aa-bb-cc-11-22-33',
    'aabbcc112233', 'aabb.ccdd.eeff', with optional whitespace.

    Invalid or non-48-bit input is returned stripped, with only its case changed.

    Args:
        mac: Raw MAC address string.
        uppercase: Whether to return uppercase characters (default True).

    Returns:
        Standard colon-separated 6-octet MAC address string, or the stripped
        input when it cannot be normalized.
    """
    stripped = mac.strip()
    formatted = _canonical_mac(stripped)
    if formatted is None:
        formatted = stripped
    return formatted.upper() if uppercase else formatted.lower()


def hide_mac(mac: str | None) -> str:
    """Mask a MAC address for safe logging, obscuring sensitive hardware octets.

    Masks the first 4 octets of a valid 6-octet MAC address with asterisks while
    preserving the trailing octets to distinguish devices in logs. Accepts formats
    such as 'AA:BB:CC:11:22:33', 'aa-bb-cc-11-22-33', 'aabbcc112233', with optional
    whitespace.

    Args:
        mac: Raw or formatted MAC address string, or None.

    Returns:
        Masked MAC address string (e.g., '**:**:**:**:22:33'), an empty string
        if the input is blank, or '****' when the input is not a valid MAC address.
    """
    if not mac or not (stripped := mac.strip()):
        return ""
    if stripped.startswith("**") and MASKED_MAC_PATTERN.fullmatch(stripped):
        return stripped.upper()
    canonical = _canonical_mac(stripped)
    return f"**:**:**:**:{canonical[-5:].upper()}" if canonical else "****"


def mac_addresses_match(mac1: str, mac2: str) -> bool:
    """Check if two MAC addresses match, case- and separator-insensitively.

    Args:
        mac1: First MAC address.
        mac2: Second MAC address.

    Returns:
        True if both MAC addresses are valid and represent the same hardware address.
    """
    canonical1 = _canonical_mac(mac1.strip())
    canonical2 = _canonical_mac(mac2.strip())
    if canonical1 is None or canonical2 is None:
        return False
    return canonical1 == canonical2


def is_valid_mac(mac: str) -> bool:
    """Validate that the string represents a valid 6-octet MAC address.

    Args:
        mac: The MAC address string to validate.

    Returns:
        True if the MAC address consists of 6 valid hexadecimal octets.
    """
    return bool(MAC_VALID_PATTERN.fullmatch(mac.strip()))


def normalize_local_name(name: str | None, *, case_sensitive: bool = False) -> str:
    """Normalize a BLE local name by stripping whitespace and control characters.

    Args:
        name: Raw advertisement local name string or None.
        case_sensitive: Whether to preserve case (True) or convert to casefold (False).

    Returns:
        Cleaned local name string, or empty string if input is None or blank.
    """
    if not name:
        return ""
    cleaned = name.strip().strip("\x00")
    return cleaned if case_sensitive else cleaned.casefold()


def local_names_match(
    name1: str | None,
    name2: str | None,
    *,
    case_sensitive: bool = False,
) -> bool:
    """Check if two BLE local names match with optional case sensitivity.

    Args:
        name1: First local name string.
        name2: Second local name string.
        case_sensitive: Whether comparison should be case sensitive (default False).

    Returns:
        True if normalized names match.
    """
    norm1 = normalize_local_name(name1, case_sensitive=case_sensitive)
    norm2 = normalize_local_name(name2, case_sensitive=case_sensitive)
    return bool(norm1) and norm1 == norm2


def is_karofi_local_name(name: str | None) -> bool:
    """Check if a local name matches known Karofi device name prefixes.

    Performs case-insensitive prefix matching against supported prefixes
    such as 'tcm'.

    Args:
        name: BLE advertisement local name or device name.

    Returns:
        True if the name matches a known Karofi prefix.
    """
    if normalized := normalize_local_name(name, case_sensitive=False):
        return any(normalized.startswith(prefix) for prefix in SUPPORTED_LOCAL_NAME_PREFIXES)
    return False


FILTER_STAGE_KEYS: Final[tuple[str, ...]] = tuple(stage.value for stage in KarofiFilter)


def filter_index_to_key(index: int) -> str:
    """Convert a 0-based filter index to a standardized stage key.

    Args:
        index: 0-based index of the filter.

    Returns:
        Standardized string key for the filter stage (e.g. 'filter_1', 'filter_4').
    """
    if 0 <= index < len(FILTER_STAGE_KEYS):
        return FILTER_STAGE_KEYS[index]
    return f"{FILTER_PREFIX}{index + 1}"


def is_valid_filter_key(key: str) -> bool:
    """Check if key is a canonical filter stage key (filter_<positive_ascii_int>).

    Args:
        key: Candidate filter key string.

    Returns:
        True if valid filter key without leading zeros, False otherwise.
    """
    if not key.startswith(FILTER_PREFIX):
        return False
    suffix = key.removeprefix(FILTER_PREFIX)
    return suffix.isascii() and suffix.isdigit() and not suffix.startswith("0")


def filter_key_to_index(key: str) -> int:
    """Convert a stage key to its corresponding 0-based filter index.

    Args:
        key: Filter stage key (e.g. 'filter_1', 'filter_4').

    Returns:
        0-based integer index.

    Raises:
        ValueError: If key is not a recognized filter key or valid stage number.
    """
    if not is_valid_filter_key(key):
        raise ValueError(f"Invalid filter key: {key}")
    if key in FILTER_STAGE_KEYS:
        return FILTER_STAGE_KEYS.index(key)
    suffix = key.removeprefix(FILTER_PREFIX)
    return int(suffix) - 1
