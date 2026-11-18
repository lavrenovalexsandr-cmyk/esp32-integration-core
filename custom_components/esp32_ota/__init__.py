"""ESP32 OTA manager integration."""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml
from homeassistant.components import frontend, mqtt, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DEVICE_IDENTIFIER_NAMESPACE,
    DOMAIN,
    IDENTITY_TOPIC,
    OTA_COMMAND_TOPIC,
    OTA_STATUS_TOPIC,
    PLATFORMS,
    TOKEN_SECRET_KEY,
    PANEL_STATIC_URL,
    PANEL_URL_PATH,
    PANEL_WEB_COMPONENT,
)

_LOGGER = logging.getLogger(__name__)
DATA_DEVICES = "devices"
DATA_UNSUB = "unsub"
MAX_TOKEN_LENGTH = 512
URL_RE = re.compile(
    r"^(?:https://[^\s]+\.bin(?:[?#][^\s]*)?"
    r"|https://api\.github\.com/repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"/releases/assets/[0-9]+(?:\?[^\s]*)?)$",
    re.IGNORECASE,
)
ID_RE = re.compile(r"^[0-9A-F]{12}$")


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    data = hass.data[DOMAIN][entry.entry_id] = {
        DATA_DEVICES: {}, DATA_UNSUB: [], "entry": entry,
        "entity_ids": set(), "add_entities": None,
        "ignored_devices": set(entry.options.get("ignored_devices", [])),
    }
    signal = f"{DOMAIN}_{entry.entry_id}_update"
    device_registry = dr.async_get(hass)

    def migrate_gateway_identifier(device_id: str) -> None:
        """Replace the legacy integration-specific identifier on an existing device."""
        legacy_identifier = (DOMAIN, device_id)
        shared_identifier = (DEVICE_IDENTIFIER_NAMESPACE, device_id)
        device = device_registry.async_get_device(identifiers={legacy_identifier})
        if device is None or shared_identifier in device.identifiers:
            return

        conflicting_device = device_registry.async_get_device(
            identifiers={shared_identifier}
        )
        if conflicting_device is not None and conflicting_device.id != device.id:
            _LOGGER.error(
                "Cannot migrate ESP32 %s to the shared device identifier: "
                "that identifier is already assigned to another Home Assistant device. "
                "Resolve the duplicate device entry before retrying.",
                device_id,
            )
            return

        identifiers = (device.identifiers - {legacy_identifier}) | {shared_identifier}
        try:
            device_registry.async_update_device(
                device.id, new_identifiers=identifiers
            )
            _LOGGER.info(
                "Migrated ESP32 %s to the shared Home Assistant device identifier",
                device_id,
            )
        except HomeAssistantError:
            _LOGGER.exception(
                "Unable to migrate the Home Assistant device identifier for ESP32 %s",
                device_id,
            )

    async def update_device(device_id: str, values: dict[str, Any]) -> None:
        if device_id in data["ignored_devices"]:
            return
        migrate_gateway_identifier(device_id)
        device = data[DATA_DEVICES].setdefault(device_id, {
            "id": device_id, "name": f"ESP32-{device_id[-6:]}", "model": "ESP32",
            "available": False,
        })
        device.update(values)
        async_dispatcher_send(hass, signal, device_id)
        callback = data.get("add_entities")
        if callback and device_id not in data["entity_ids"]:
            from .sensor import Esp32OtaSensor
            data["entity_ids"].add(device_id)
            callback([Esp32OtaSensor(hass, entry, device_id)])

    async def identity_message(msg: mqtt.ReceiveMessage) -> None:
        try:
            payload = json.loads(msg.payload)
            device_id = str(payload.get("id", "")).upper()
            name = str(payload.get("name", "")).strip()
            model = str(payload.get("model", "ESP32"))
            if not ID_RE.fullmatch(device_id) or not name:
                return
            await update_device(device_id, {"name": name[:64], "model": model[:64]})
        except (ValueError, TypeError, AttributeError):
            _LOGGER.warning("Ignoring invalid ESP32 identity message")

    async def availability_message(msg: mqtt.ReceiveMessage) -> None:
        match = re.fullmatch(r"esp32/([0-9A-Fa-f]{12})/availability", msg.topic)
        if not match:
            return
        payload = str(msg.payload).strip().lower()
        if payload not in ("online", "offline"):
            _LOGGER.warning("Ignoring invalid ESP32 availability payload")
            return
        await update_device(match.group(1).upper(), {"available": payload == "online"})

    async def status_message(msg: mqtt.ReceiveMessage) -> None:
        match = re.fullmatch(r"esp32/([0-9A-Fa-f]{12})/ota/status", msg.topic)
        if not match:
            return
        try:
            payload = json.loads(msg.payload)
            if not isinstance(payload, dict):
                return
            values = {"ota_status": str(payload.get("status", "unknown"))[:64]}
            if isinstance(payload.get("progress"), (int, float)):
                values["progress"] = max(0, min(100, int(payload["progress"])))
            if payload.get("message") is not None:
                values["message"] = str(payload["message"])[:256]
            await update_device(match.group(1).upper(), values)
        except (ValueError, TypeError):
            _LOGGER.warning("Ignoring invalid OTA status message")

    data[DATA_UNSUB] = [
        await mqtt.async_subscribe(hass, IDENTITY_TOPIC, identity_message, qos=1),
        await mqtt.async_subscribe(hass, "esp32/+/availability", availability_message, qos=1),
        await mqtt.async_subscribe(hass, OTA_STATUS_TOPIC, status_message, qos=1),
    ]
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data[DOMAIN].get("_static_path_registered"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    PANEL_STATIC_URL,
                    str(Path(__file__).parent / "www" / "ota-panel.js"),
                    cache_headers=False,
                )
            ]
        )
        hass.data[DOMAIN]["_static_path_registered"] = True

    if not hass.data[DOMAIN].get("_panel_registered"):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_WEB_COMPONENT,
            sidebar_title="ESP32 OTA",
            sidebar_icon="mdi:chip",
            module_url=f"{PANEL_STATIC_URL}?v=1",
            require_admin=True,
        )
        hass.data[DOMAIN]["_panel_registered"] = True

    async def handle_update(call: ServiceCall) -> None:
        device_id = str(call.data["device_id"]).upper()
        url = str(call.data["url"]).strip()
        token = str(call.data.get("token", "")).strip()
        if device_id not in data[DATA_DEVICES]:
            raise HomeAssistantError("ESP32 не обнаружена. Проверьте MQTT и подключение устройства.")
        if not URL_RE.fullmatch(url):
            raise HomeAssistantError("Укажите прямой HTTPS URL файла прошивки с расширением .bin.")
        if token:
            await _save_token(hass, token)
        stored_token = token or await _read_token(hass)
        command = {"url": url}
        if stored_token:
            command["github_token"] = stored_token
        await mqtt.async_publish(
            hass, OTA_COMMAND_TOPIC.format(device_id=device_id),
            json.dumps(command, separators=(",", ":")), qos=1, retain=False
        )

    async def publish_device_command(call: ServiceCall, command: str) -> None:
        device_id = str(call.data["device_id"]).upper()
        if not ID_RE.fullmatch(device_id):
            raise HomeAssistantError("Некорректный ID ESP32.")
        if device_id not in data[DATA_DEVICES]:
            raise HomeAssistantError("ESP32 не обнаружена. Проверьте MQTT и подключение устройства.")
        topic = f"esp32/{device_id}/system/{command}"
        await mqtt.async_publish(hass, topic, "1", qos=1, retain=False)
        _LOGGER.warning("Sent %s command to ESP32 %s", command, device_id)

    async def handle_restart(call: ServiceCall) -> None:
        await publish_device_command(call, "restart")

    async def handle_factory_reset(call: ServiceCall) -> None:
        await publish_device_command(call, "factory_reset")

    async def handle_save_token(call: ServiceCall) -> None:
        await _save_token(hass, str(call.data.get("token", "")).strip())

    async def handle_remove_device(call: ServiceCall) -> None:
        device_id = str(call.data["device_id"]).upper()
        if not ID_RE.fullmatch(device_id):
            raise HomeAssistantError("Некорректный ID ESP32.")

        device_entry = device_registry.async_get_device(
            identifiers={(DEVICE_IDENTIFIER_NAMESPACE, device_id)}
        )
        if device_entry is None:
            raise HomeAssistantError("Устройство не найдено в реестре Home Assistant.")

        # Remove every entity attached to this shared ESP32 gateway, including
        # entities contributed by other ESP32 integrations.
        entity_registry = er.async_get(hass)
        for entity in er.async_entries_for_device(
            entity_registry, device_entry.id, include_disabled_entities=True
        ):
            entity_registry.async_remove(entity.entity_id)

        # Clear runtime state before removing the registry entry. The next MQTT
        # identity message can then create a fresh entity for the same device.
        data[DATA_DEVICES].pop(device_id, None)
        data["entity_ids"].discard(device_id)
        ignored = set(data["ignored_devices"])
        if device_id in ignored:
            ignored.discard(device_id)
            data["ignored_devices"] = ignored
            hass.config_entries.async_update_entry(
                entry,
                options={**entry.options, "ignored_devices": sorted(ignored)},
            )

        device_registry.async_remove_device(device_entry.id)
        _LOGGER.info(
            "Removed ESP32 %s and its Home Assistant device registry entry; "
            "MQTT rediscovery remains enabled",
            device_id,
        )

    hass.services.async_register(DOMAIN, "update", handle_update, schema=vol.Schema({
        vol.Required("device_id"): str, vol.Required("url"): str,
        vol.Optional("token", default=""): str,
    }))
    device_schema = vol.Schema({vol.Required("device_id"): str})
    hass.services.async_register(DOMAIN, "restart", handle_restart, schema=device_schema)
    hass.services.async_register(DOMAIN, "factory_reset", handle_factory_reset, schema=device_schema)
    hass.services.async_register(DOMAIN, "save_token", handle_save_token,
                                 schema=vol.Schema({vol.Required("token"): str}))
    hass.services.async_register(
        DOMAIN, "remove_device", handle_remove_device, schema=device_schema
    )
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing an ESP32 gateway while keeping MQTT rediscovery enabled."""
    device_id = next(
        (
            identifier[1].upper()
            for identifier in device_entry.identifiers
            if identifier[0] == DEVICE_IDENTIFIER_NAMESPACE
            and ID_RE.fullmatch(identifier[1].upper())
        ),
        None,
    )
    if device_id is None:
        return False

    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is None:
        return False

    entity_registry = er.async_get(hass)
    for entity in er.async_entries_for_device(
        entity_registry, device_entry.id, include_disabled_entities=True
    ):
        entity_registry.async_remove(entity.entity_id)

    data[DATA_DEVICES].pop(device_id, None)
    data["entity_ids"].discard(device_id)
    ignored = set(data["ignored_devices"])
    if device_id in ignored:
        ignored.discard(device_id)
        data["ignored_devices"] = ignored
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "ignored_devices": sorted(ignored)}
        )

    _LOGGER.info(
        "ESP32 %s removed from Home Assistant; MQTT rediscovery remains enabled",
        device_id,
    )
    return True

async def _read_token(hass: HomeAssistant) -> str:
    path = Path(hass.config.config_dir) / "secrets.yaml"
    if not path.exists():
        return ""
    try:
        content = await hass.async_add_executor_job(path.read_text, "utf-8")
        parsed = yaml.safe_load(content) or {}
        value = parsed.get(TOKEN_SECRET_KEY, "") if isinstance(parsed, dict) else ""
        return value if isinstance(value, str) else ""
    except Exception:
        _LOGGER.exception("Unable to read ESP32 OTA token from secrets.yaml")
        return ""


async def _save_token(hass: HomeAssistant, token: str) -> None:
    if len(token) > MAX_TOKEN_LENGTH:
        raise HomeAssistantError("GitHub token слишком длинный.")
    path = Path(hass.config.config_dir) / "secrets.yaml"

    def write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text("utf-8") if path.exists() else ""
        lines = old.splitlines(keepends=True)
        pattern = re.compile(r"^" + re.escape(TOKEN_SECRET_KEY) + r"\s*:")
        replacement = f"{TOKEN_SECRET_KEY}: {json.dumps(token, ensure_ascii=True)}\n" if token else ""
        output, found = [], False
        for line in lines:
            if pattern.match(line):
                if not found and replacement:
                    output.append(replacement)
                found = True
            else:
                output.append(line)
        if not found and replacement:
            if output and not output[-1].endswith("\n"):
                output[-1] += "\n"
            output.append(replacement)
        fd, temp_name = tempfile.mkstemp(prefix=".secrets-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("".join(output))
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    await hass.async_add_executor_job(write)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    if hass.data.get(DOMAIN, {}).get("_panel_registered"):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        hass.data[DOMAIN]["_panel_registered"] = False

    data = hass.data[DOMAIN].pop(entry.entry_id, {})
    for unsub in data.get(DATA_UNSUB, []):
        unsub()
    hass.services.async_remove(DOMAIN, "update")
    hass.services.async_remove(DOMAIN, "save_token")
    hass.services.async_remove(DOMAIN, "restart")
    hass.services.async_remove(DOMAIN, "factory_reset")
    hass.services.async_remove(DOMAIN, "remove_device")
    return True
