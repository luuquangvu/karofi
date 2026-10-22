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


# Makipos protocol frame literals and keys
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

TELEMETRY_FILTER_MARKERS: Final[tuple[bytes, ...]] = (
    MakiposPayloadKey.IRO_RTR.encode(),
    MakiposPayloadKey.IRO_LTIME.encode(),
)

# Polling and command frames (Makipos / Karofi protocol)
REQUEST_READ_DATA_COMMAND: Final = (
    f'\x01{{"{MakiposPayloadKey.EVENT_TYPE}":"{MakiposEventType.NP}"}}\x04'.encode()
)


def build_reset_filter_command(filter_index: int) -> bytes:
    """Construct the Makipos BLE command to reset a filter stage.

    Args:
        filter_index: 0-based integer index of the filter to reset.

    Returns:
        Framed SOH/EOT command bytes.
    """
    return (
        f'\x01{{"{MakiposPayloadKey.EVENT_TYPE}":"{MakiposEventType.CP}",'
        f'"{MakiposPayloadKey.PROPERTY_CODE}":"{MakiposPropertyCode.IRO_RSFT}",'
        f'"{MakiposPayloadKey.DATA}":{filter_index}}}\x04'.encode()
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
    RESTORE = "mdi:restore"


class KarofiDevicePrefix(StrEnum):
    """Known Karofi device local name prefix."""

    TCM = "tcm"


# Storage constants
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = "karofi_filters"

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


# Polling and connection settings
DEFAULT_SCAN_INTERVAL: Final = 60
BLE_CONNECT_ATTEMPTS: Final = 2
BLE_TIMEOUT_SECONDS: Final = 25
SUPPLEMENTAL_TELEMETRY_TIMEOUT_SECONDS: Final = 10
DIRECT_TDS_RETURN_RESERVE_SECONDS: Final = 2


# Config flow options
MANUAL_MAC_CHOICE: Final = "manual"
MANUAL_MAC_LABEL: Final = "Enter MAC address manually..."

# Filter stage prefix and identifiers
FILTER_PREFIX: Final = "filter_"


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
