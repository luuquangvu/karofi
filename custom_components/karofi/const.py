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


TDS_PAYLOAD_KEYS: Final[tuple[str, ...]] = (
    MakiposPayloadKey.IRO_TDS,
    MakiposPayloadKey.TDS,
    MakiposPayloadKey.PURE_INDEX,
    MakiposPayloadKey.PI,
)

# Polling and command frames (Makipos / Karofi protocol)
REQUEST_READ_DATA_COMMAND: Final = b'\x01{"et":"NP"}\x04'


# GATT Service & Characteristic UUIDs (Makipos / Karofi firmware)
class KarofiCharacteristic(StrEnum):
    """GATT Service and Characteristic UUIDs for Karofi devices."""

    SERVICE = "000012ac-0000-1000-8000-00805f9b34fb"
    TDS_SENSOR = "000012ac-0000-1000-8000-00805f9b34fb"
    COMMAND = "000012ab-0000-1000-8000-00805f9b34fb"
    NOTIFY = "0000ac02-0000-1000-8000-00805f9b34fb"
    WIFI_PROV_RX = "0000ab01-0000-1000-8000-00805f9b34fb"
    WIFI_STATUS_TX = "0000ab02-0000-1000-8000-00805f9b34fb"
    OTA_FIRMWARE = "0000ac02-0000-1000-8000-00805f9b34fb"
    CCCD = "00002902-0000-1000-8000-00805f9b34fb"


class KarofiShortUUID(StrEnum):
    """16-bit Short GATT Service and Characteristic UUIDs for Karofi devices."""

    SERVICE = "12ac"
    TDS_SENSOR = "12ac"
    COMMAND = "12ab"
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


# Config flow options
MANUAL_MAC_CHOICE: Final = "manual"
MANUAL_MAC_LABEL: Final = "Enter MAC address manually..."


class KarofiFilter(StrEnum):
    """Karofi water purifier filter stage identifiers."""

    FILTER_1 = "filter_1"
    FILTER_2 = "filter_2"
    FILTER_3 = "filter_3"
    FILTER_RO = "filter_ro"
    FILTER_MINERAL = "filter_mineral"


# Filter lifetime configurations (in days)
FILTER_LIFETIME_DAYS: Final[dict[KarofiFilter, int]] = {
    KarofiFilter.FILTER_1: 180,
    KarofiFilter.FILTER_2: 270,
    KarofiFilter.FILTER_3: 270,
    KarofiFilter.FILTER_RO: 730,
    KarofiFilter.FILTER_MINERAL: 365,
}
