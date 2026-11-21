[Русский](README.md) | [English](README_EN.md)

# ESP32 Manager for Home Assistant

A Home Assistant custom integration for discovering ESP32 devices over MQTT, displaying their status, and managing OTA firmware updates, device restarts, factory resets, and full removal from Home Assistant while keeping rediscovery enabled.

## Repository contents

- `custom_components/esp32_ota` — Home Assistant integration and device-control services.

The **ESP32 Manager** control panel is included in this integration and appears in the Home Assistant sidebar. No separate Lovelace card or second HACS repository is required.

## ESP32 ecosystem architecture

ESP32 Manager is the central home for cross-project ecosystem contracts: physical device identity, product profiles, MQTT interactions, firmware transitions, and universal OTA management.

- [ESP32 ecosystem architecture: identity, profiles, and firmware management](docs/ECOSYSTEM_ARCHITECTURE_RU.md)

Internal APIs and components of ESP32 Platform Core are documented separately in the [Core repository](https://github.com/lavrenovalexsandr-cmyk/esp32-platform-core).

## Requirements

- Home Assistant with the official MQTT integration configured and connected.
- ESP32 firmware implementing the matching MQTT device-identity and OTA topics.
- Restart and factory-reset controls additionally require firmware support for the `system/restart` and `system/factory_reset` topics.

## Gateway identity in Home Assistant

All ESP32 integrations use the same gateway identifier in the Home Assistant Device Registry:

```python
identifiers={("esp32", device_id)}
```

- `esp32` is the platform-wide ESP32 namespace, not an integration-specific domain.
- `device_id` is the stable gateway ID from MQTT identity.
- **OTA Manager is responsible for the gateway's initial registration and for the initial user-assigned name** when the device is first discovered in Home Assistant.
- The user-assigned name is stored in the Home Assistant Device Registry as `name_by_user` and is the single shared display name for the gateway across all ESP32 integrations.
- The MQTT identity `name` is the device's original technical name and a fallback when `name_by_user` has not been set. It must never overwrite the user-assigned name.
- Other ESP32 integrations, including Xiaomi Sensor Manager, must read `name_by_user` from the shared Device Registry entry and must not maintain a separate user-assigned gateway name.

Rename the gateway through Home Assistant's device page (**Settings → Devices & services → Devices → ESP32 → Edit device**). The new name must then be displayed by all integrations using the shared identifier. Renaming does not change the MQTT identity or the stable `device_id`.

**Requirement for the `esp32_ota` implementation:** whenever the integration updates the gateway entry, it must preserve `name_by_user`. Calling `async_get_or_create` with the MQTT name may create/update the device's original identity data, but must not cause the user-assigned name to be lost.

**Migration in this branch:** on the first MQTT message from a gateway, the integration automatically replaces the legacy identifier with the shared one while preserving the existing device entry and its entities. If the shared identifier already belongs to a different HA device entry, the automatic change is skipped and an error is logged; such duplicates must not be merged blindly. Update `esp32_ota` before installing/updating other integrations that use the new namespace. The integration and service domain `esp32_ota` remains unchanged.

Other ESP32 integrations must use the same identifier and obtain identity directly from the shared MQTT contract. They must not import `esp32_ota` code or require it to be installed. Integrations can independently add their entities to the same gateway device.
## Device availability

The integration tracks MQTT availability separately through `esp32/<DEVICE_ID>/availability` (retained `online` / `offline`). OTA status is not used to determine device availability. If the ESP32 disconnects unexpectedly, the broker publishes retained `offline` through the MQTT Last Will configured by the Core `platform_ha` component. The **ESP32 OTA** panel displays “Offline” regardless of the last update result.

## Installation through HACS

1. Open **HACS → Integrations**.
2. Open the menu in the upper-right corner and select **Custom repositories**.
3. Add this repository:
   `https://github.com/lavrenovalexsandr-cmyk/esp32-integration-core`
4. Select **Integration** as the category and confirm.
5. Find **ESP32 Manager** in HACS and install it.
6. Restart Home Assistant.
7. Go to **Settings → Devices & services → Add integration** and add **ESP32 Manager**.
8. Open the **ESP32 OTA** panel from the Home Assistant sidebar.

HACS installs the integration files into `custom_components/esp32_ota`. Future versions can be updated directly from HACS.

### Manual installation

If you do not use HACS, copy `custom_components/esp32_ota` into `<HA config>/custom_components/esp32_ota`, restart Home Assistant, and add the integration from **Settings → Devices & services → Add integration**.

## Home Assistant control panel

The **ESP32 OTA** panel is shipped with the integration, registered in the Home Assistant sidebar, and restricted to administrators. It combines ESP32 selection, MQTT availability, OTA status and progress, firmware update, GitHub token storage, restart, and factory reset. The panel calls integration services and never publishes MQTT directly. Removing a gateway clears its Device Registry entry and associated entities; a subsequent MQTT identity message from the ESP32 can discover it again.

The panel reads `name_by_user` from the shared Device Registry entry identified by `("esp32", device_id)` and falls back to the device's original name when no user-assigned name exists.

## Home Assistant services

The integration provides the following services in the `esp32_ota` domain:

| Service | Parameters | Description |
|---|---|---|
| `esp32_ota.update` | `device_id`, `url`, optional `token` | Start an OTA update on the selected device |
| `esp32_ota.restart` | `device_id` | Request a normal reboot of the selected ESP32 |
| `esp32_ota.factory_reset` | `device_id` | Request a factory reset of the selected ESP32 |
| `esp32_ota.save_token` | `token` | Save the GitHub token |
| `esp32_ota.remove_device` | `device_id` | Remove the Home Assistant device entry and associated entities while keeping MQTT rediscovery enabled |

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

The firmware-side OTA receiver is maintained in the ESP32 firmware project: [esp32_mqtt_xiaomi_lywsd03](https://github.com/lavrenovalexsandr-cmyk/esp32-firmware-xiaomi-lywsd03). It downloads firmware over HTTPS, writes the image to the inactive OTA partition, and reports update status over MQTT.

## GitHub token for private releases

The **ESP32 OTA** panel accepts an optional GitHub token. The integration can save it under `esp32_ota_github_token` in Home Assistant's `secrets.yaml`. Existing lines and comments are preserved, and the file is written atomically with owner-only permissions. A token entered directly in the update form overrides the saved token for that request.

Tokens are sent only to GitHub API endpoints; they must not be forwarded to external CDN hosts.
