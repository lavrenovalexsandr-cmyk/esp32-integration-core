[Русский](README.md) | [English](README_EN.md)

# ESP32 OTA Manager for Home Assistant

Home Assistant custom integration and Lovelace card for discovering ESP32 devices over MQTT and managing firmware updates.

## Repository contents

- `custom_components/esp32_ota` — Home Assistant integration.
- `www/esp32-ota-card.js` — Lovelace card for OTA updates and live device status.

## Requirements

- Home Assistant with the official MQTT integration configured and connected.
- ESP32 firmware implementing the matching MQTT identity, OTA command, and OTA status topics.

## Installation

1. Copy `custom_components/esp32_ota` into `<HA config>/custom_components/esp32_ota`.
2. Restart Home Assistant.
3. Add **ESP32 OTA Manager** from Settings → Devices & services → Add integration.
4. Copy `www/esp32-ota-card.js` into `<HA config>/www/esp32-ota-card.js`.
5. Add the Lovelace resource `/local/esp32-ota-card.js` as a JavaScript module.
6. Add a manual Lovelace card:

```yaml
type: custom:esp32-ota-card
title: ESP32 firmware updates
```

## MQTT contract

The integration listens for retained device identity messages:

- Topic: `esp32/<12-uppercase-hex-id>/device/info`
- Example payload:

```json
{"id":"AABBCCDDEEFF","name":"Kitchen","model":"ESP32-C3"}
```

OTA command (QoS 1, not retained):

- Topic: `esp32/<id>/ota/update`
- Payload:

```json
{"url":"https://.../firmware.bin","sha256":"<64 hex>","github_token":"<optional>"}
```

OTA status:

- Topic: `esp32/<id>/ota/status`
- Example payload:

```json
{"status":"online|downloading|verifying|installing|rebooting|success|error","progress":0,"message":"..."}
```

The firmware-side OTA receiver is maintained in the ESP32 firmware project: [esp32_mqtt_xiaomi_lywsd03](https://github.com/lavrenovalexsandr-cmyk/esp32_mqtt_xiaomi_lywsd03). It downloads firmware over HTTPS, verifies SHA-256, writes the image to the inactive OTA partition, and reports update status over MQTT.

## GitHub token for private releases

The card accepts an optional GitHub token. The integration can save it under `esp32_ota_github_token` in Home Assistant's `secrets.yaml`. Existing lines and comments are preserved, and the file is written atomically with owner-only permissions. A token entered directly in the update form overrides the saved token for that request.

Tokens are sent only to GitHub API endpoints; they must not be forwarded to external CDN hosts.

## Firmware integrity

Always provide the SHA-256 digest of the exact firmware binary being installed. The ESP32 firmware must verify the digest before activating the new OTA slot.
