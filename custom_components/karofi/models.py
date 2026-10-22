"""Data models for the Karofi integration."""

from dataclasses import dataclass, field
from datetime import datetime

from .const import WaterQuality


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
