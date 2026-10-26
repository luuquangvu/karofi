# Karofi Water Purifier for Home Assistant

**🇺🇸 English | [🇻🇳 Tiếng Việt](README.vi.md)**

**Karofi Water Purifier** provides local Bluetooth Low Energy (BLE) integration for Karofi smart water purifiers in Home Assistant. Monitor purified water Total Dissolved Solids (TDS), track drinking water quality ratings, and monitor multi-stage filter lifespans (5 to 12+ stages) using calendar life and runtime usage reported by the device, with zero cloud dependence.

---

## Table of Contents

- [Features](#features)
- [Hardware Compatibility](#hardware-compatibility)
- [Entities Provided](#entities-provided)
- [Device Error Codes](#device-error-codes)
- [Installation](#installation)
  - [Option 1: Using HACS (Recommended)](#option-1-using-hacs-recommended)
  - [Option 2: Manual Installation](#option-2-manual-installation)
- [Setup and Configuration](#setup-and-configuration)
- [Filter Lifetime Management](#filter-lifetime-management)
- [Automation Example](#automation-example)
- [Requirements](#requirements)
- [Code Quality and Reliability](#code-quality-and-reliability)
- [Contributing](#contributing)
- [License](#license)
- [Support the Project](#support-the-project)

---

## Features

- **100% Local Control (No Cloud Required)**: Communicates directly via Bluetooth Low Energy (BLE) using Home Assistant's native Bluetooth stack. No proprietary hubs, external cloud accounts, or internet connectivity needed.
- **Real-Time Pure Water TDS Monitoring**: Accurately measures Total Dissolved Solids in parts per million (ppm) directly from the purifier's sensor after the RO membrane.
- **Bluetooth Signal Strength**: Shows how strong the purifier's Bluetooth signal is, in dBm, so you can check coverage and find the best place for a Bluetooth proxy.
- **Health-Graded Water Quality Classification**: Automatically categorizes water purity into actionable ratings:
  - **Good** (< 50 ppm): High-purity drinking water with optimal mineral balance.
  - **Fair** (50-150 ppm): Acceptable drinking water; monitor for gradual increases.
  - **Poor** (> 150 ppm): Water purity compromised; inspection or cartridge replacement recommended.
  - **Unknown**: Waiting for telemetry or sensor unavailable.
- **Dynamic Multi-Stage Filter Tracking (5, 7, 8, 9, 10, 11, 12+ filters)**:
  - Dynamically discovers and tracks filter cartridges from device telemetry.
  - Simple, clean naming (`Filter 1`, `Filter 2`, `Filter 3`, etc.) that adapts dynamically to your actual model without misleading hardcoded cartridge types.
- **Triple Lifespan Indicators**: Every filter stage provides a remaining life percentage sensor (0% to 100%), a calendar days remaining sensor, and a runtime usage hours remaining sensor for intuitive dashboard cards and automations.
- **Interactive Maintenance Controls**:
  - **One-Click Reset Buttons**: Press the reset button in Home Assistant when replacing a physical cartridge to send the reset command to the device.
- **Clear Error Alerts**: If your purifier has a problem, a Problem sensor turns on. Open it to see the error code and a short explanation in your Home Assistant language.
- **Persistent Local Storage**: Filter reset timestamps and maintenance state are saved to Home Assistant's persistent storage (`.storage`), ensuring zero data loss across restarts or reboots.
- **Bluetooth Proxy and ESPHome Support**: Fully compatible with Home Assistant Bluetooth Proxies (ESPHome or Shelly BLE Proxies), allowing reliable reception even when your water purifier is located far from the server.
- **Resilient BLE Engine**: Employs `bleak-retry-connector` with multi-frame JSON packet stream parsing, write-without-response fallbacks, and connection timeout guardrails to handle noisy 2.4 GHz environments smoothly.
- **Bilingual Interface**: Native translations in both English (`en`) and Vietnamese (`vi`).

---

## Hardware Compatibility

This integration is designed for Karofi smart water purifiers equipped with Makipos Bluetooth Low Energy (BLE) controllers:

- **Advertised Local Names**: Devices broadcast with the prefix `TCM-*` (such as `TCM-112233`).
- **Bluetooth Service UUID**: `000012ac-0000-1000-8000-00805f9b34fb`.
- **Compatible Models**: Karofi smart water purifier models with Bluetooth connectivity and compatible smart controllers.

---

## Entities Provided

| Platform          | Identifier                                                                  | Device Class / Unit | Description                                                                                            |
| :---------------- | :-------------------------------------------------------------------------- | :------------------ | :----------------------------------------------------------------------------------------------------- |
| **Sensor**        | `sensor.<device_name>_pure_water_tds` (Entity ID pattern)                   | `ppm`               | Real-time pure water TDS concentration.                                                                |
| **Sensor**        | `sensor.<device_name>_water_quality` (Entity ID pattern)                    | `enum`              | Water quality rating (`good`, `fair`, `poor`, `unknown`).                                              |
| **Sensor**        | `sensor.<device_name>_signal_strength` (Entity ID pattern)                  | `dBm`               | Bluetooth signal strength in dBm, read from device advertisements. Diagnostic sensor.                  |
| **Sensor**        | `sensor.<device_name>_filter_<n>_life` (Entity ID pattern)                  | `%`                 | Filter `<n>` remaining life percentage (dynamically added).                                            |
| **Sensor**        | `sensor.<device_name>_filter_<n>_days_remaining` (Entity ID pattern)        | `days`              | Filter `<n>` remaining calendar days until replacement.                                                |
| **Sensor**        | `sensor.<device_name>_filter_<n>_usage_hours_remaining` (Entity ID pattern) | `hours`             | Filter `<n>` remaining runtime usage hours.                                                            |
| **Binary Sensor** | `binary_sensor.<device_name>_connectivity` (Entity ID pattern)              | `connectivity`      | Bluetooth connection status with the purifier. Diagnostic sensor.                                      |
| **Binary Sensor** | `binary_sensor.<device_name>_problem` (Entity ID pattern)                   | `problem`           | Turns on when the purifier reports a problem. Its details show the error code and a short explanation. |
| **Button**        | `button.<device_name>_reset_filter_<n>` (Entity ID pattern)                 | `restore`           | Send a reset command for the specified filter stage.                                                   |

---

## Device Error Codes

If something goes wrong, the purifier reports an error code. The **Problem** sensor turns on, and its details show the code together with a short explanation in your Home Assistant language.

| Code | What it means                                    |
| ---: | :----------------------------------------------- |
|    1 | The pump has been running for more than 5 hours. |
|    2 | The incoming water pressure is low.              |
|    4 | No water is reaching the purifier.               |
|    5 | The purifier may be leaking.                     |
|  100 | The TDS level of the filtered water is high.     |

When everything is working normally, the sensor stays off. If the purifier goes offline, the sensor will show as unavailable until it reconnects. If you see a code that is not listed, Home Assistant will show `Unknown device error code <code>` in your language. If the problem keeps coming back, contact Karofi support.

---

## Installation

### Option 1: Using HACS (Recommended)

[![Add Karofi to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=luuquangvu&repository=karofi&category=integration)

1. Open **HACS** in Home Assistant.
2. Click the top-right menu icon and select **Custom repositories**.
3. Add repository URL: `https://github.com/luuquangvu/karofi` with category **Integration**.
4. Search for **Karofi Water Purifier** and click **Download**.
5. Restart Home Assistant.

### Option 2: Manual Installation

1. Download the latest release from the [Releases page](https://github.com/luuquangvu/karofi/releases).
2. Extract the archive and copy the `custom_components/karofi` directory into your Home Assistant `<config_dir>/custom_components/` folder.
3. Restart Home Assistant.

---

## Setup and Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. If your Karofi purifier is nearby or within range of an ESPHome Bluetooth Proxy, Home Assistant will **automatically discover** it. Click **Configure** on the discovered device card.
3. If not automatically discovered, click **Add Integration** and search for **Karofi**.
4. Select your discovered purifier from the drop-down list, or choose **"Enter MAC address manually..."** to input the 6-octet Bluetooth MAC address (e.g., `AA:BB:CC:11:22:33`).
5. Confirm the setup. Your entities will immediately be created and mapped to the device.

> [!NOTE]
> Filter data may take a few background scans to settle after setup. Initial readings can be incomplete or inaccurate while filter stages and telemetry are being discovered; wait a few minutes for the values to update.

---

## Filter Lifetime Management

Filter timer values are reported in hours:

- **Calendar remaining days** = `max(0, round(iro_lTime / 24))`
- **Calendar capacity days** = `max(1, round(iro_hsd / 24))`
- **Runtime remaining hours** = `iro_rtr`
- **Runtime capacity hours** = `iro_rtt`

The calendar percentage uses `iro_lTime` and `iro_hsd`; a stored reset date is used only as a fallback before remaining-time telemetry is available. The runtime percentage uses `iro_rtr` and `iro_rtt`. The life percentage is the lower of the two, so heavy usage can shorten the effective life even when calendar time remains.

$$\text{Calendar Percent} = \max\left(0, \min\left(100, \text{round}\left(\frac{\text{Calendar Remaining Hours}}{\text{Calendar Rated Hours}} \times 100\right)\right)\right)$$

$$\text{Runtime Percent} = \max\left(0, \min\left(100, \text{round}\left(\frac{\text{Runtime Remaining Hours}}{\text{Runtime Rated Hours}} \times 100\right)\right)\right)$$

$$\text{Life Percent} = \min\left(\text{Calendar Percent}, \text{Runtime Percent}\right)$$

The days remaining sensor stays calendar-based. The remaining sensors expose the rated capacities as attributes: `capacity_days` on the days sensor, `capacity_hours` on the usage hours sensor, and both capacities plus `calendar_percent` and `runtime_percent` on the life sensor.

> [!NOTE]
> The life percentage now considers both calendar life and runtime usage. This is a behavior change for heavy users, who may see the percentage drop faster than before. Some models do not report runtime telemetry; the life sensor then falls back to calendar life without errors.

### When Replacing a Filter Cartridge

1. Navigate to the Karofi device page in **Settings** > **Devices & Services** > **Karofi**.
2. Under the **Buttons** section, press the corresponding reset button (e.g., `Reset filter 1`).
3. The integration sends the reset command to the purifier. After `async_reset_filter` succeeds, the local reset value remains pending while telemetry is stale or has not confirmed the reset; confirmed device telemetry then becomes the source of truth.

### When Synchronizing Existing Filters

Remaining days are read from the purifier and cannot be edited through the integration. If they appear incorrect, reset the corresponding filter on the purifier and allow telemetry to update; a Home Assistant reset button can reset a filter when it is physically replaced.

---

## Automation Example

Receive a mobile notification when the drinking water TDS indicates poor quality, or dynamically monitor all discovered filter cartridges and alert when any filter has less than 15 days remaining:

```yaml
automation:
  - id: karofi_filter_replacement_alert
    alias: "Karofi: Filter Replacement Needed"
    # Runs daily to check if any discovered filter stage (5 to 12+) has less than 15 days remaining
    trigger:
      - platform: time
        at: "09:00:00"
    condition:
      - condition: template
        value_template: >-
          {{ integration_entities('karofi')
            | select('match', '^sensor\.')
            | expand
            | selectattr('attributes.unit_of_measurement', 'eq', 'd')
            | selectattr('state', 'is_number')
            | map(attribute='state')
            | map('int')
            | select('lt', 15)
            | list | count > 0 }}
    action:
      - action: notify.notify
        data:
          title: "Water Filter Replacement Reminder"
          message: >-
            The following filter cartridge(s) need replacement soon:
            {% for item in integration_entities('karofi')
               | select('match', '^sensor\.')
               | expand
               | selectattr('attributes.unit_of_measurement', 'eq', 'd')
               | selectattr('state', 'is_number') %}
              {% if item.state | int < 15 %}
              - {{ item.name }}: {{ item.state }} days remaining
              {% endif %}
            {% endfor %}
            Please prepare replacement cartridges.

  - id: karofi_high_tds_alert
    alias: "Karofi: High TDS Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.karofi_water_purifier_pure_water_tds
        above: 150
        for:
          minutes: 2
    action:
      - action: notify.notify
        data:
          title: "High TDS Warning"
          message: "Pure water TDS reading is {{ trigger.to_state.state }} ppm (Poor). Check the RO membrane and water line."
```

---

## Requirements

- **Home Assistant**: `2026.10.0` or newer.
- **Bluetooth**: Local Bluetooth adapter or [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy.html) / Shelly Bluetooth Gateway.
- **Python**: `>= 3.14` (Managed automatically by Home Assistant).

---

## Code Quality and Reliability

To ensure long-term reliability and stability, this project utilizes a modern stack of automated development and security tools:

- **Automated Code Review**: [CodeRabbit AI](https://coderabbit.ai) provides deep analysis of every Pull Request, identifying potential logic flaws and edge cases before they reach your system.
- **Code Optimization**: [Sourcery AI](https://sourcery.ai) suggests cleaner, more idiomatic Python patterns to maintain a high-quality codebase.
- **Static Analysis & Security**: [CodeQL](https://codeql.github.com) performs industry-standard scans to detect security vulnerabilities and ensure compliance with best practices.
- **Rigorous Development Workflow**:
  - **[Ruff](https://github.com/astral-sh/ruff)**: High-performance linting and formatting for consistent Python code.
  - **[Ty](https://github.com/astral-sh/ty)** & **[Pyright](https://github.com/Microsoft/pyright)**: Dual-layer type checking to catch type errors before runtime and ensure API stability.
  - **[Pytest](https://github.com/pytest-dev/pytest)**: A comprehensive test suite ensuring every update is functional and regression-free.
  - **[Interrogate](https://github.com/econchick/interrogate)**: Docstring coverage enforcement across the entire codebase to keep the code self-documenting.
  - **[Prettier](https://github.com/prettier/prettier)**: Consistent formatting for documentation and configuration files.

> [!NOTE]
> All automated insights are manually reviewed and validated by the project maintainer to ensure every change aligns with the project's standards.

---

## Contributing

Contributions are welcomed! If you encounter an issue or have a feature suggestion:

1. Check existing issues or [open a new issue](https://github.com/luuquangvu/karofi/issues).
2. For code contributions, fork the repository, make your changes, and submit a Pull Request. Please ensure validation pipeline pass via `uv run --locked tools/validate.py`.

---

## License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

---

## Support the Project

If you find this integration helpful for your smart home setup, consider supporting its maintenance and development:

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/luuquangvu89)
