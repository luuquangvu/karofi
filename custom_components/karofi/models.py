"""Data models for the Karofi integration."""

from dataclasses import dataclass
from datetime import datetime

from .const import WaterQuality


@dataclass
class KarofiData:
    """Class representing parsed Karofi water purifier status."""

    tds: int | None
    quality: WaterQuality | str
    connected: bool
    last_seen: datetime | None
    filter_reset_dates: dict[str, str]
