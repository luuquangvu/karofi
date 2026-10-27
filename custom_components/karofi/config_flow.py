"""Config flow for Karofi integration."""

import probatio as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_MAC, CONF_NAME
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
)

from .const import (
    DEFAULT_NAME,
    DOMAIN,
    MANUAL_MAC_CHOICE,
    MANUAL_MAC_LABEL,
    KarofiCharacteristic,
)
from .util import (
    is_karofi_local_name,
    is_valid_mac,
    normalize_local_name,
    normalize_mac,
)


class KarofiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Karofi."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovered_name: str = DEFAULT_NAME

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Handle bluetooth discovery step when device is spotted by BLE Proxy.

        Args:
            discovery_info: Discovery information provided by Bluetooth scanner.

        Returns:
            Flow result proceeding to confirmation or aborting.
        """
        formatted_mac = normalize_mac(discovery_info.address, uppercase=False)
        await self.async_set_unique_id(formatted_mac)
        self._abort_if_unique_id_configured()

        cleaned_name = normalize_local_name(discovery_info.name, case_sensitive=True)
        name = cleaned_name or f"Karofi ({formatted_mac[-5:]})"
        self.context["title_placeholders"] = {"name": name}
        self._discovered_name = name
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        """Confirm adding a discovered Karofi device.

        Args:
            user_input: Form input containing confirmation.

        Returns:
            Flow result creating entry or showing confirmation form.
        """
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovered_name,
                data={
                    CONF_MAC: self.unique_id,
                    CONF_NAME: self._discovered_name,
                },
            )

        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context.get("title_placeholders", {}),
        )

    def _discover_device_choices(self) -> dict[str, str]:
        """Discover available Karofi Bluetooth devices that are not yet configured.

        Returns:
            Dictionary mapping formatted MAC addresses to display labels.
        """
        discovered = async_discovered_service_info(self.hass, connectable=True)
        configured_macs = self._async_current_ids()
        device_choices: dict[str, str] = {}
        for dev in discovered:
            fmt = normalize_mac(dev.address, uppercase=False)
            if fmt in configured_macs:
                continue
            is_karofi = is_karofi_local_name(dev.name) or any(
                uuid.lower() == KarofiCharacteristic.SERVICE.lower() for uuid in dev.service_uuids
            )
            if not is_karofi:
                continue
            cleaned_dev_name = normalize_local_name(dev.name, case_sensitive=True)
            label = f"{cleaned_dev_name} ({fmt})" if cleaned_dev_name else fmt
            device_choices[fmt] = label
        return device_choices

    async def _async_create_entry_from_input(
        self,
        raw_mac: str,
        raw_name: object,
    ) -> ConfigFlowResult:
        """Normalize input and create the Karofi config entry.

        Args:
            raw_mac: User-provided or selected MAC address.
            raw_name: Optional user-provided display name.

        Returns:
            Config entry creation result.
        """
        formatted_mac = normalize_mac(raw_mac, uppercase=False)
        await self.async_set_unique_id(formatted_mac)
        self._abort_if_unique_id_configured()

        user_name = normalize_local_name(
            str(raw_name) if raw_name is not None else None,
            case_sensitive=True,
        )
        name = user_name or f"Karofi ({formatted_mac[-5:]})"
        return self.async_create_entry(
            title=name,
            data={CONF_MAC: formatted_mac, CONF_NAME: name},
        )

    async def async_step_user(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        """Handle manual user initiation.

        Args:
            user_input: User submitted configuration dictionary.

        Returns:
            Flow result creating entry or displaying device selection.
        """
        if user_input is not None:
            mac_input = str(user_input[CONF_MAC]).strip()
            if mac_input == MANUAL_MAC_CHOICE:
                return await self.async_step_manual()
            return await self._async_create_entry_from_input(
                mac_input,
                user_input.get(CONF_NAME),
            )

        device_choices = self._discover_device_choices()
        if not device_choices:
            return await self.async_step_manual()

        options = [
            SelectOptionDict(value=mac, label=label) for mac, label in device_choices.items()
        ]
        options.append(SelectOptionDict(value=MANUAL_MAC_CHOICE, label=MANUAL_MAC_LABEL))

        schema = vol.Schema(
            {
                vol.Required(CONF_MAC): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        translation_key="device_selection",
                    )
                ),
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_manual(
        self,
        user_input: dict[str, object] | None = None,
    ) -> ConfigFlowResult:
        """Handle manual MAC address input.

        Args:
            user_input: User entered MAC and name.

        Returns:
            Flow result creating entry or displaying manual entry form.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_mac = str(user_input[CONF_MAC]).strip()
            if not is_valid_mac(raw_mac):
                errors[CONF_MAC] = "invalid_mac"
            else:
                return await self._async_create_entry_from_input(
                    raw_mac,
                    user_input.get(CONF_NAME),
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_MAC): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )

        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)
