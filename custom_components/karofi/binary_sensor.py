"""Binary sensor platform for the Karofi integration."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_translate_state_attr

from .const import DOMAIN, describe_device_error
from .coordinator import KarofiDataCoordinator
from .entity import KarofiBaseEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Karofi binary sensors from a config entry.

    Args:
        hass: HomeAssistant instance.
        entry: Integration config entry.
        async_add_entities: Callback to register entities.
    """
    coordinator: KarofiDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            KarofiConnectivityBinarySensor(coordinator, entry),
            KarofiProblemBinarySensor(coordinator, entry),
        ]
    )


class KarofiConnectivityBinarySensor(KarofiBaseEntity, BinarySensorEntity):
    """Binary sensor indicating device Bluetooth connectivity."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the connectivity binary sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_connectivity"
        self._attr_is_on = coordinator.data.connected

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self.coordinator.data.connected
        super()._handle_coordinator_update()


class KarofiProblemBinarySensor(KarofiBaseEntity, BinarySensorEntity):
    """Binary sensor indicating whether the purifier reports an error."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, coordinator: KarofiDataCoordinator, entry: ConfigEntry) -> None:
        """Initialize the problem binary sensor.

        Args:
            coordinator: Coordinator providing data.
            entry: Configuration entry.
        """
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_problem"
        self._attr_is_on = bool(coordinator.data.errors)
        self._attr_available = coordinator.data.connected
        self._attr_extra_state_attributes = self._error_attributes()

    async def async_added_to_hass(self) -> None:
        """Refresh error attributes once Home Assistant translations are loaded."""
        await super().async_added_to_hass()
        self._attr_extra_state_attributes = self._error_attributes()

    def _translate_error_message(self, value: str) -> str | None:
        """Translate one error-message value, if a translation exists.

        Args:
            value: Error code or fallback translation key.

        Returns:
            Translated message, or None when no translation is available.
        """
        if self.hass is None:
            return None
        translated = async_translate_state_attr(
            self.hass,
            value,
            "binary_sensor",
            DOMAIN,
            "problem",
            None,
            "error_messages",
        )
        return None if translated == value else translated

    def _error_message(self, code: int) -> str:
        """Return a localized description for a device error code.

        Args:
            code: Device error code reported in ``iro_errs``.

        Returns:
            Localized error description, or the English vendor message fallback.
        """
        if (translated := self._translate_error_message(str(code))) is not None:
            return translated
        if (unknown := self._translate_error_message("unknown")) is not None:
            return f"{unknown} {code}"
        return describe_device_error(code)

    def _error_attributes(self) -> dict[str, object]:
        """Return active error codes and their localized descriptions."""
        errors = self.coordinator.data.errors
        return {
            "error_count": len(errors),
            "error_codes": list(errors),
            "error_messages": [self._error_message(code) for code in errors],
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = bool(self.coordinator.data.errors)
        self._attr_available = self.coordinator.data.connected
        self._attr_extra_state_attributes = self._error_attributes()
        super()._handle_coordinator_update()
