"""Data models for the Karofi integration."""

from dataclasses import dataclass, field
from datetime import datetime

from bleak.backends.characteristic import BleakGATTCharacteristic

from .const import WaterQuality


@dataclass
class PendingFilterReset:
    """Pending device-side filter reset waiting for telemetry confirmation."""

    generation: int
    mismatch_count: int = 0
    last_counted_generation: int | None = None
    lifetime_hours: int | None = None
    baseline_hours: int | None = None


@dataclass
class PendingResetOutcome:
    """Outcome of evaluating telemetry against a pending filter reset."""

    is_stale: bool = False
    use_pending_value: bool = False
    resolved: bool = False
    abandoned: bool = False


@dataclass
class ResolvedGattTargets:
    """Resolved GATT characteristics used for reads, notifications, and commands."""

    read_target: BleakGATTCharacteristic | str
    notify_target: BleakGATTCharacteristic | str
    command_target: BleakGATTCharacteristic | str
    read_characteristic: BleakGATTCharacteristic | None
    can_read: bool


@dataclass
class KarofiFilterData:
    """Class representing individual filter stage telemetry and status."""

    index: int
    key: str
    name: str | None = None
    remaining_hours: int | None = None
    lifetime_hours: int | None = None
    total_hours: int | None = None
    remaining_days: int | None = None
    lifetime_days: int | None = None
    percent: int | None = None
    reset_date: str | None = None
    telemetry_synced: bool = False


@dataclass
class KarofiData:
    """Class representing parsed Karofi water purifier status."""

    tds: int | None
    quality: WaterQuality | str
    connected: bool
    last_seen: datetime | None
    filter_reset_dates: dict[str, str]
    filters: dict[str, KarofiFilterData] = field(default_factory=dict)
    errors: list[int] = field(default_factory=list)
