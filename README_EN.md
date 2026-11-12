[Русский](README.md) | [English](README_EN.md)

# ESP32 OTA Manager for Home Assistant

Home Assistant custom integration and Lovelace cards for discovering ESP32 devices over MQTT, displaying their status, managing OTA firmware updates, restarting devices, and requesting a factory reset.

## Repository contents

- `custom_components/esp32_ota` — Home Assistant integration and device-control services.
- `www/esp32-ota-card.js` — Lovelace card for OTA updates and live device status.
- `www/esp32-device-control-card.js` — standalone Lovelace card for restarting an ESP32 or requesting a factory reset.

## Requirements

- Home Assistant with the official MQTT integration configured and connected.
- ESP32 firmware implementing the matching MQTT device-identity and OTA topics.
- Restart and factory-reset controls additionally require firmware support for the `system/restart` and `system/factory_reset` topics.

## Installation

1. Copy `custom_components/esp32_ota` into `<HA config>/custom_components/esp32_ota`.
2. Restart Home Assistant.
3. Add **ESP32 OTA Manager** from Settings → Devices & services → Add integration.
4. Copy both files from the repository's `www` directory into `<HA config>/www/`:
   - `esp32-ota-card.js`
   - `esp32-device-control-card.js`
5. Add both Lovelace resources as **JavaScript modules**:

```yaml
resources:
  - url: /local/esp32-ota-card.js
    type: module
  - url: /local/esp32-device-control-card.js
    type: module
```

6. Add the OTA card manually:

```yaml
type: custom:esp32-ota-card
title: ESP32 firmware updates
```

7. Add the separate device-control card:

```yaml
type: custom:esp32-device-control-card
title: ESP32 Device Control
```

The control card automatically builds its device list from ESP32 devices discovered by the integration. Select a device and choose an action:
- **Restart** requests a normal reboot of the selected ESP32.
- **Factory Reset** requests a factory reset. Two confirmation dialogs are displayed before the command is sent.

The cards are independent and can be placed on the same or on separate Lovelace dashboards.

## Home Assistant services

The integration provides the following services in the `esp32_ota` domain:

| Service | Parameters | Description |
|---|---|---|
| `esp32_ota.update` | `device_id`, `url`, optional `token` | Start an OTA update on the selected device |
| `esp32_ota.restart` | `device_id` | Request a normal reboot of the selected ESP32 |
| `esp32_ota.factory_reset` | `device_id` | Request a factory reset of the selected ESP32 |
| `esp32_ota.save_token` | `token` | Save the GitHub token |

For device-control services, `device_id` is the 12-character hexadecimal hardware ID of the ESP32. The device must have been discovered by the integration over MQTT.

## MQTT contract

The integration listens for retained device identity messages:

- Topic: `esp32/<12-uppercase-hex-id>/device/info`
- Example payload:

```json
{"id":"AABBCCDDEEFF","name":"Kitchen","model":"ESP32-C3"}
```

### OTA updates

OTA command (QoS 1, not retained):

- Topic: `esp32/<id>/ota/update`
- Payload:

```json
{"url":"https://.../firmware.bin","github_token":"<optional>"}
```

OTA status:

- Topic: `esp32/<id>/ota/status`
- Example payload:

```json
{"status":"online|downloading|installing|rebooting|success|error","progress":0,"message":"..."}
```

### Device-control commands

Commands are addressed to the selected ESP32 and published with QoS 1 and retain disabled:

| Action | MQTT topic | Payload |
|---|---|---|
| Restart | `esp32/<id>/system/restart` | `1` |
| Factory reset | `esp32/<id>/system/factory_reset` | `1` |

A normal restart must not modify device settings. A factory reset must be handled by the firmware and remove user settings according to that firmware's implementation. The Home Assistant integration only publishes the command; it does not erase ESP32 storage itself. Make sure the installed firmware version supports these MQTT commands.

Successful service execution confirms that the command was published to MQTT; it does not confirm that the device has completed the restart or factory reset.

The firmware-side OTA receiver is maintained in the ESP32 firmware project: [esp32_mqtt_xiaomi_lywsd03](https://github.com/lavrenovalexsandr-cmyk/esp32_mqtt_xiaomi_lywsd03). It downloads firmware over HTTPS, writes the image to the inactive OTA partition, and reports update status over MQTT.

## GitHub token for private releases

The OTA card accepts an optional GitHub token. The integration can save it under `esp32_ota_github_token` in Home Assistant's `secrets.yaml`. Existing lines and comments are preserved, and the file is written atomically with owner-only permissions. A token entered directly in the update form overrides the saved token for that request.

Tokens are sent only to GitHub API endpoints; they must not be forwarded to external CDN hosts.

## Firmware integrity

Always provide the SHA-256 digest of the exact firmware binary being installed. The ESP32 firmware must verify the digest before activating the new OTA slot.
