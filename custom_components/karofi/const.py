"""Constants for the Karofi integration."""

from enum import StrEnum
from typing import Final

from homeassistant.const import UnitOfRatio

DOMAIN: Final = "karofi"

# Device identification defaults
DEFAULT_NAME: Final = "Karofi Water Purifier"
DEFAULT_MANUFACTURER: Final = "Karofi / Makipos"
DEFAULT_MODEL: Final = "Karofi Water Purifier"

# Measurement units
UNIT_PPM: Final = UnitOfRatio.PARTS_PER_MILLION


# Makipos protocol payload keys
class MakiposPayloadKey(StrEnum):
    """Makipos protocol JSON payload dictionary keys."""

    PROPERTY_CODE = "pc"
    EVENT_TYPE = "et"
    DATA = "d"
    ID = "id"
    IRO_TDS = "iro_tds"
    IRO_RTR = "iro_rtr"
    IRO_LTIME = "iro_lTime"
    IRO_RTT = "iro_rtt"
    IRO_HSD = "iro_hsd"
    IRO_ERRS = "iro_errs"
    IRO_NAMES = "iro_names"
    FILTER_NAMES = "filter_names"
    NAMES = "names"
    TDS = "tds"
    PURE_INDEX = "pure_index"
    PI = "pi"


class MakiposEventType(StrEnum):
    """Makipos protocol event types."""

    NP = "NP"
    CP = "CP"


class MakiposPropertyCode(StrEnum):
    """Makipos protocol property codes."""

    IRO_TDS = "iro_tds"
    IRO_RTR = "iro_rtr"
    IRO_LTIME = "iro_lTime"
    IRO_RTT = "iro_rtt"
    IRO_HSD = "iro_hsd"
    IRO_ERRS = "iro_errs"
    IRO_RSFT = "iro_rsft"


# Only identifiers consumed by the coordinator belong in these sets. Recognized
# but unprocessed protocol fields remain reportable by unknown-payload logging.
SUPPORTED_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {
        MakiposPayloadKey.PROPERTY_CODE.value,
        MakiposPayloadKey.EVENT_TYPE.value,
        MakiposPayloadKey.DATA.value,
        MakiposPayloadKey.ID.value,
        MakiposPayloadKey.IRO_TDS.value,
        MakiposPayloadKey.IRO_RTR.value,
        MakiposPayloadKey.IRO_LTIME.value,
        MakiposPayloadKey.IRO_RTT.value,
        MakiposPayloadKey.IRO_HSD.value,
        MakiposPayloadKey.IRO_ERRS.value,
        MakiposPayloadKey.IRO_NAMES.value,
        MakiposPayloadKey.FILTER_NAMES.value,
        MakiposPayloadKey.NAMES.value,
        MakiposPayloadKey.TDS.value,
        MakiposPayloadKey.PURE_INDEX.value,
        MakiposPayloadKey.PI.value,
    }
)
SUPPORTED_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        MakiposEventType.NP.value,
        MakiposEventType.CP.value,
    }
)
SUPPORTED_PROPERTY_CODES: Final[frozenset[str]] = frozenset(
    {
        MakiposPropertyCode.IRO_TDS.value,
        MakiposPropertyCode.IRO_RTR.value,
        MakiposPropertyCode.IRO_LTIME.value,
        MakiposPropertyCode.IRO_RTT.value,
        MakiposPropertyCode.IRO_HSD.value,
        MakiposPropertyCode.IRO_ERRS.value,
    }
)


TDS_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    MakiposPayloadKey.IRO_TDS,
    MakiposPayloadKey.TDS,
    MakiposPayloadKey.PURE_INDEX,
    MakiposPayloadKey.PI,
)

TELEMETRY_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    *TDS_PAYLOAD_KEYS,
    MakiposPayloadKey.PROPERTY_CODE,
    MakiposPayloadKey.IRO_RTR,
    MakiposPayloadKey.IRO_LTIME,
    MakiposPayloadKey.IRO_RTT,
    MakiposPayloadKey.IRO_HSD,
    MakiposPayloadKey.IRO_ERRS,
    MakiposPayloadKey.IRO_NAMES,
    MakiposPayloadKey.FILTER_NAMES,
    MakiposPayloadKey.NAMES,
)

# Polling and command payloads (Makipos / Karofi protocol)
REQUEST_READ_DATA_COMMAND: Final = (
    f'{{"{MakiposPayloadKey.EVENT_TYPE}":"{MakiposEventType.NP}"}}'.encode()
)
MAKIPOS_COMMAND_START: Final = b"\x01"
MAKIPOS_COMMAND_TERMINATOR: Final = b"\x04"


def build_reset_filter_command(filter_index: int) -> bytes:
    """Construct the Makipos BLE command to reset a filter stage.

    Args:
        filter_index: 0-based integer index of the filter to reset.

    Returns:
        Framed SOH/EOT command bytes.

    Raises:
        ValueError: If the filter index is negative.
    """
    if filter_index < 0:
        raise ValueError("Filter index must be non-negative")

    return (
        MAKIPOS_COMMAND_START
        + (
            f'{{"{MakiposPayloadKey.EVENT_TYPE}":"{MakiposEventType.CP}",'
            f'"{MakiposPayloadKey.PROPERTY_CODE}":"{MakiposPropertyCode.IRO_RSFT}",'
            f'"{MakiposPayloadKey.DATA}":{filter_index}}}'
        ).encode()
        + MAKIPOS_COMMAND_TERMINATOR
    )


# GATT Service & Characteristic UUIDs (Makipos / Karofi firmware)
class KarofiCharacteristic(StrEnum):
    """GATT Service and Characteristic UUIDs for Karofi devices."""

    SERVICE = "000012ac-0000-1000-8000-00805f9b34fb"
    TDS_SENSOR = "000012ac-0000-1000-8000-00805f9b34fb"
    COMMAND_SERVICE = "000012ab-0000-1000-8000-00805f9b34fb"
    COMMAND = "0000ab02-0000-1000-8000-00805f9b34fb"
    NOTIFY = "0000ac02-0000-1000-8000-00805f9b34fb"
    WIFI_PROV_RX = "0000ab01-0000-1000-8000-00805f9b34fb"
    WIFI_STATUS_TX = "0000ab02-0000-1000-8000-00805f9b34fb"
    OTA_FIRMWARE = "0000ac02-0000-1000-8000-00805f9b34fb"
    CCCD = "00002902-0000-1000-8000-00805f9b34fb"


class KarofiShortUUID(StrEnum):
    """16-bit Short GATT Service and Characteristic UUIDs for Karofi devices."""

    SERVICE = "12ac"
    TDS_SENSOR = "12ac"
    COMMAND_SERVICE = "12ab"
    COMMAND = "ab02"
    NOTIFY = "ac02"
    WIFI_PROV_RX = "ab01"
    WIFI_STATUS_TX = "ab02"
    OTA_FIRMWARE = "ac02"
    CCCD = "2902"


NOTIFY_UUID_PREFIX: Final = "0000ac02"


class GattProperty(StrEnum):
    """BLE GATT characteristic property strings."""

    READ = "read"
    NOTIFY = "notify"
    WRITE = "write"
    WRITE_WITHOUT_RESPONSE = "write-without-response"
    INDICATE = "indicate"


class KarofiIcon(StrEnum):
    """Standard MDI icons for Karofi entities."""

    TDS = "mdi:water-opacity"
    QUALITY = "mdi:water-check"
    FILTER = "mdi:filter-outline"
    CALENDAR = "mdi:calendar-clock"
    CLOCK = "mdi:clock-outline"
    RESTORE = "mdi:restore"
    SIGNAL = "mdi:signal"


class KarofiDevicePrefix(StrEnum):
    """Known Karofi device local name prefix."""

    TCM = "tcm"


# Storage constants
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = "karofi_filters"
PENDING_RESET_STORAGE_SUFFIX: Final = "pending_resets"

# Water Quality Thresholds (ppm)
TDS_THRESHOLD_GOOD: Final = 50
TDS_THRESHOLD_FAIR: Final = 150
MAX_VALID_TDS: Final = 2000
TDS_SENTINEL_ERROR: Final = 0xFFFF


class WaterQuality(StrEnum):
    """Water quality rating categories."""

    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    UNKNOWN = "unknown"


# English fallback messages from the Karofi 365 app's water-purifier mapping.
DEVICE_ERROR_MESSAGES: Final[dict[int, str]] = {
    1: "Pump works continuously for more than 5 hours",
    2: "Weak inlet water",
    4: "No inlet water",
    5: "The water is leaking",
    100: "TDS high",
}


def describe_device_error(code: int) -> str:
    """Return the English fallback description for a device error code.

    Args:
        code: Device error code reported in ``iro_errs``.

    Returns:
        English vendor error description, or a generic fallback for unknown codes.
    """
    return DEVICE_ERROR_MESSAGES.get(code, f"Unknown device error code {code}")


# Polling and connection settings
DEFAULT_SCAN_INTERVAL: Final = 60
BLE_CONNECT_ATTEMPTS: Final = 2
BLE_TIMEOUT_SECONDS: Final = 25
BLE_COMMAND_CHUNK_SIZE: Final = 19
SUPPLEMENTAL_TELEMETRY_TIMEOUT_SECONDS: Final = 10
DIRECT_TDS_RETURN_RESERVE_SECONDS: Final = 2
NOTIFICATION_WAIT_SECONDS: Final = 3.5
NOTIFICATION_TELEMETRY_GRACE_SECONDS: Final = 0.5
MAX_TELEMETRY_CACHE_ENTRIES: Final = 8
FILTER_DATA_STALE_AFTER_SECONDS: Final = 3600
WARNING_LOG_INTERVAL_SECONDS: Final = 3600
FULL_TELEMETRY_SCAN_INTERVAL: Final = 60


# Config flow options
MANUAL_MAC_CHOICE: Final = "manual"
MANUAL_MAC_LABEL: Final = "Enter MAC address manually..."

# Filter stage prefix and identifiers
FILTER_PREFIX: Final = "filter_"
MAX_FILTER_STAGES: Final = 16


class KarofiFilter(StrEnum):
    """Karofi water purifier filter stage identifiers."""

    FILTER_1 = "filter_1"
    FILTER_2 = "filter_2"
    FILTER_3 = "filter_3"
    FILTER_4 = "filter_4"
    FILTER_5 = "filter_5"
    FILTER_6 = "filter_6"
    FILTER_7 = "filter_7"
    FILTER_8 = "filter_8"
    FILTER_9 = "filter_9"
    FILTER_10 = "filter_10"
    FILTER_11 = "filter_11"
    FILTER_12 = "filter_12"


# Default filter lifetime configurations (in days) when not provided by telemetry
DEFAULT_FILTER_LIFETIME_DAYS: Final[dict[str, int]] = {
    KarofiFilter.FILTER_1: 180,
    KarofiFilter.FILTER_2: 270,
    KarofiFilter.FILTER_3: 270,
    KarofiFilter.FILTER_4: 730,
    KarofiFilter.FILTER_5: 365,
    KarofiFilter.FILTER_6: 365,
    KarofiFilter.FILTER_7: 365,
    KarofiFilter.FILTER_8: 365,
    KarofiFilter.FILTER_9: 365,
    KarofiFilter.FILTER_10: 365,
    KarofiFilter.FILTER_11: 365,
    KarofiFilter.FILTER_12: 365,
}
