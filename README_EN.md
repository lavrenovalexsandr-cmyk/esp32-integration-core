[Русский](README.md) | [English](README_EN.md)

# ESP32 OTA Manager for Home Assistant

A Home Assistant custom integration for discovering ESP32 devices over MQTT, displaying their status, and managing OTA firmware updates, device restarts, and factory resets.

## Repository contents

- `custom_components/esp32_ota` — Home Assistant integration and device-control services.

The Lovelace cards have been moved to a separate repository: [esp32-card](https://github.com/lavrenovalexsandr-cmyk/esp32-card).

## Requirements

- Home Assistant with the official MQTT integration configured and connected.
- ESP32 firmware implementing the matching MQTT device-identity and OTA topics.
- Restart and factory-reset controls additionally require firmware support for the `system/restart` and `system/factory_reset` topics.

## Installation through HACS

1. Open **HACS → Integrations**.
2. Open the menu in the upper-right corner and select **Custom repositories**.
3. Add this repository:
   `https://github.com/lavrenovalexsandr-cmyk/esp32-mqtt-ha-integration`
4. Select **Integration** as the category and confirm.
5. Find **ESP32 OTA Manager** in HACS and install it.
6. Restart Home Assistant.
7. Go to **Settings → Devices & services → Add integration** and add **ESP32 OTA Manager**.
8. Install the Lovelace cards separately from the [esp32-card](https://github.com/lavrenovalexsandr-cmyk/esp32-card) repository.

HACS installs the integration files into `custom_components/esp32_ota`. Future versions can be updated directly from HACS.

### Manual installation

If you do not use HACS, copy `custom_components/esp32_ota` into `<HA config>/custom_components/esp32_ota`, restart Home Assistant, and add the integration from **Settings → Devices & services → Add integration**.

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
