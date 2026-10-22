# Karofi Water Purifier for Home Assistant

**🇺🇸 English | [🇻🇳 Tiếng Việt](README.vi.md)**

**Karofi Water Purifier** provides local Bluetooth Low Energy (BLE) integration for Karofi smart water purifiers in Home Assistant. Monitor purified water Total Dissolved Solids (TDS), track drinking water quality ratings, manage multi-stage filter lifespans (5 to 12+ stages) in both percentage and remaining days, and adjust or reset maintenance schedules directly from your Home Assistant dashboard with zero cloud dependence.

---

## Table of Contents

- [Features](#features)
- [Hardware Compatibility](#hardware-compatibility)
- [Entities Provided](#entities-provided)
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
- **Health-Graded Water Quality Classification**: Automatically categorizes water purity into actionable ratings:
  - **Good** (< 50 ppm): High-purity drinking water with optimal mineral balance.
  - **Fair** (50-150 ppm): Acceptable drinking water; monitor for gradual increases.
  - **Poor** (> 150 ppm): Water purity compromised; inspection or cartridge replacement recommended.
  - **Unknown**: Waiting for telemetry or sensor unavailable.
- **Dynamic Multi-Stage Filter Tracking (5, 7, 8, 9, 10, 11, 12+ filters)**:
  - Dynamically discovers and tracks filter cartridges from device telemetry.
  - Simple, clean naming (`Filter 1`, `Filter 2`, `Filter 3`, etc.) that adapts dynamically to your actual model without misleading hardcoded cartridge types.
- **Dual Lifespan Indicators**: Every filter stage provides both a remaining percentage sensor (0% to 100%) and a remaining days sensor for intuitive dashboard cards and automations.
- **Interactive Maintenance Controls**:
  - **One-Click Reset Buttons**: Press the reset button in Home Assistant when replacing a physical cartridge to refresh the installation date to today.
  - **Adjustable Days Sliders**: Directly fine-tune or synchronize remaining days using configuration number entities without having to reset from scratch.
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

| Platform          | Entity ID                                    | Device Class / Unit | Description                                                                     |
| :---------------- | :------------------------------------------- | :------------------ | :------------------------------------------------------------------------------ |
| **Sensor**        | `sensor.<mac>_tds`                           | `ppm`               | Real-time pure water TDS concentration.                                         |
| **Sensor**        | `sensor.<mac>_quality`                       | `enum`              | Water quality rating (`good`, `fair`, `poor`, `unknown`).                       |
| **Sensor**        | `sensor.<mac>_filter_<n>_percent`            | `%`                 | Filter `<n>` remaining life percentage (dynamically added).                     |
| **Sensor**        | `<mac>_filter_<n>_days` (unique ID)          | `days`              | Filter `<n>` remaining days until replacement; `_days` is the unique-ID suffix. |
| **Binary Sensor** | `binary_sensor.<mac>_connectivity`           | `connectivity`      | Bluetooth connection status with the purifier.                                  |
| **Number**        | `number.<mac>_num_filter_<n>_remaining_days` | `duration` (`days`) | Configuration slider to adjust remaining filter lifespan.                       |
| **Button**        | `button.<mac>_btn_reset_filter_<n>`          | `restore`           | Reset the installation date for the specified filter stage.                     |

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

---

## Filter Lifetime Management

Each filter stage calculates its lifespan using the formula:

$$\text{Elapsed Days} = \text{Current Date} - \text{Filter Reset Date}$$
$$\text{Remaining Days} = \max\left(0, \text{Max Lifespan} - \text{Elapsed Days}\right)$$
$$\text{Remaining Percent} = \max\left(0, \min\left(100, \text{round}\left(\frac{\text{Remaining Days}}{\text{Max Lifespan}} \times 100\right)\right)\right)$$

### When Replacing a Filter Cartridge

1. Navigate to the Karofi device page in **Settings** > **Devices & Services** > **Karofi**.
2. Under the **Buttons** section, click **Press** on the corresponding reset button (e.g., `Reset filter 1`).
3. The installation date is reset to current UTC time, immediately setting the remaining days to 100% of its rated lifespan.

### When Synchronizing Existing Filters

If you recently installed cartridges before setting up this integration, use the **Number slider** for that filter to slide the remaining days to match your manual records.

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
