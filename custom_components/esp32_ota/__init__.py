"""ESP32 Manager integration."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

import voluptuous as vol
import yaml
import homeassistant.helpers.config_validation as cv
from homeassistant.components import frontend, mqtt, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.components.mqtt.const import MQTT_CONNECTION_STATE

from .repositories import (
    async_register_websocket_commands,
    async_setup_repositories,
    _get_repo,
)

from .const import (
    API_VERSION,
    DATA_API,
    DATA_REMOVE_HANDLERS,
    EVENT_DEVICE_ADDED,
    EVENT_DEVICE_UPDATED,
    EVENT_DEVICE_REMOVING,
    EVENT_DEVICE_REMOVED,
    DEVICE_IDENTIFIER_NAMESPACE,
    DOMAIN,
    IDENTITY_TOPIC,
    OTA_COMMAND_TOPIC,
    PROFILE_TOPIC,
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


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    return True


async def _async_remove_device(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: dict[str, Any],
    device_id: str,
    *,
    remove_registry: bool,
) -> None:
    """Run the same coordinated removal lifecycle for service and Device Registry."""
    locks = data.setdefault("remove_locks", {})
    lock = locks.setdefault(device_id, asyncio.Lock())
    async with lock:
        hass.bus.async_fire(EVENT_DEVICE_REMOVING, {"device_id": device_id})
        device = data[DATA_DEVICES].get(device_id)
        if device is not None and device.get("available"):
            try:
                await mqtt.async_publish(
                    hass,
                    f"esp32/{device_id}/system/factory_reset",
                    "1",
                    qos=1,
                    retain=False,
                )
            except Exception:
                _LOGGER.warning(
                    "Unable to publish Factory Reset for online ESP32 %s; continuing HA cleanup",
                    device_id,
                    exc_info=True,
                )

        handlers = hass.data.get(DOMAIN, {}).get(DATA_REMOVE_HANDLERS, {})
        for integration, handler in tuple(handlers.items()):
            try:
                await handler(device_id)
            except Exception:
                _LOGGER.exception(
                    "Removal handler for integration %s failed for ESP32 %s",
                    integration,
                    device_id,
                )

        # Clear retained messages owned by ESP32 Manager. Offline devices never
        # receive a queued or deferred Factory Reset command.
        for topic in (
            f"esp32/{device_id}/device/info",
            f"esp32/{device_id}/device/profile",
            f"esp32/{device_id}/availability",
            f"esp32/{device_id}/ota/status",
        ):
            try:
                await mqtt.async_publish(hass, topic, "", qos=1, retain=True)
            except Exception:
                _LOGGER.warning(
                    "Unable to clear retained ESP32 topic %s during removal",
                    topic,
                    exc_info=True,
                )

        device_registry = dr.async_get(hass)
        device_entry = device_registry.async_get_device(
            identifiers={(DEVICE_IDENTIFIER_NAMESPACE, device_id)}
        )
        if device_entry is not None:
            entity_registry = er.async_get(hass)
            for entity in er.async_entries_for_device(
                entity_registry, device_entry.id, include_disabled_entities=True
            ):
                entity_registry.async_remove(entity.entity_id)

            # Home Assistant keeps deleted devices in its internal registry and
            # restores their name/area when the same identifiers (or connections)
            # are discovered again. Retire the stable ESP32 identity before removal
            # so a later discovery creates a genuinely new device entry.
            retired_identifier = (
                f"{DOMAIN}_removed",
                uuid.uuid4().hex,
            )
            device_registry.async_update_device(
                device_entry.id,
                new_identifiers={retired_identifier},
                new_connections=set(),
            )
            if remove_registry:
                device_registry.async_remove_device(device_entry.id)

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

        hass.bus.async_fire(EVENT_DEVICE_REMOVED, {"device_id": device_id})
        _LOGGER.info(
            "Completed ESP32 %s removal lifecycle (remove_registry=%s)",
            device_id,
            remove_registry,
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    await async_setup_repositories(hass)
    if not hass.data[DOMAIN].get("_repo_ws_registered"):
        async_register_websocket_commands(hass)
        hass.data[DOMAIN]["_repo_ws_registered"] = True
    data = hass.data[DOMAIN][entry.entry_id] = {
        DATA_DEVICES: {}, DATA_UNSUB: [], "entry": entry,
        "entity_ids": set(), "add_entities": None,
        "ignored_devices": set(entry.options.get("ignored_devices", [])),
    }
    hass.data[DOMAIN].setdefault(DATA_REMOVE_HANDLERS, {})
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
        was_identified = bool(
            data[DATA_DEVICES].get(device_id, {}).get("identity_received")
        )
        device = data[DATA_DEVICES].setdefault(device_id, {
            "id": device_id, "name": f"ESP32-{device_id[-6:]}", "model": "ESP32",
            "manufacturer": "Espressif", "sw_version": None,
            "available": False, "availability_received": False,
            "identity_received": False, "profile": None,
        })
        device.update(values)
        if device.get("identity_received"):
            device_registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DEVICE_IDENTIFIER_NAMESPACE, device_id)},
                name=device.get("name"),
                manufacturer=device.get("manufacturer", "Espressif"),
                model=device.get("model", "ESP32"),
                sw_version=device.get("sw_version"),
            )
        async_dispatcher_send(hass, signal, device_id)
        snapshot = get_device_snapshot(device_id)
        if snapshot is not None:
            hass.bus.async_fire(
                EVENT_DEVICE_ADDED if not was_identified else EVENT_DEVICE_UPDATED,
                {"device": snapshot},
            )
        callback = data.get("add_entities")
        if callback and device.get("identity_received") and device_id not in data["entity_ids"]:
            from .sensor import Esp32OtaSensor
            data["entity_ids"].add(device_id)
            callback([Esp32OtaSensor(hass, entry, device_id)])

    async def identity_message(msg: mqtt.ReceiveMessage) -> None:
        try:
            payload = json.loads(msg.payload)
            device_id = str(payload.get("id", "")).upper()
            name = str(payload.get("name", "")).strip()
            model = str(payload.get("model", "ESP32"))
            sw_version = payload.get("sw_version")
            if not ID_RE.fullmatch(device_id) or not name:
                return
            await update_device(device_id, {
                "name": name[:64], "model": model[:64],
                "sw_version": sw_version[:64] if isinstance(sw_version, str) and sw_version.strip() else None,
                "identity_received": True,
            })
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
        await update_device(match.group(1).upper(), {
            "available": payload == "online", "availability_received": True
        })

    async def profile_message(msg: mqtt.ReceiveMessage) -> None:
        """Maintain the latest product profile in the central device registry."""
        match = re.fullmatch(r"esp32/([0-9A-Fa-f]{12})/device/profile", msg.topic)
        if not match:
            return
        device_id = match.group(1).upper()
        if not msg.payload:
            await update_device(device_id, {"profile": None})
            return
        try:
            profile = json.loads(msg.payload)
        except (ValueError, TypeError):
            _LOGGER.warning("Ignoring invalid ESP32 profile message")
            return
        if not isinstance(profile, dict):
            _LOGGER.warning("Ignoring invalid ESP32 profile message")
            return
        await update_device(device_id, {"profile": profile})

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


    def get_device_snapshot(device_id: str) -> dict[str, Any] | None:
        """Return a detached, public snapshot of one known ESP32."""
        device = data[DATA_DEVICES].get(str(device_id).upper())
        if device is None or not device.get("identity_received"):
            return None
        device_id = device["id"]
        registry_entry = device_registry.async_get_device(
            identifiers={(DEVICE_IDENTIFIER_NAMESPACE, device_id)}
        )
        profile = device.get("profile")
        profile_copy = copy.deepcopy(profile) if isinstance(profile, dict) else None
        capabilities = profile_copy.get("capabilities", []) if profile_copy else []
        operations = profile_copy.get("operations", {}) if profile_copy else {}
        return {
            "device_id": device_id,
            "name": device.get("name"),
            "name_by_user": registry_entry.name_by_user if registry_entry else None,
            "manufacturer": device.get("manufacturer", "Espressif"),
            "model": device.get("model", "ESP32"),
            "sw_version": device.get("sw_version"),
            "available": bool(device.get("available", False)),
            "availability_received": bool(device.get("availability_received", False)),
            "identity_received": True,
            "profile": profile_copy,
            "product_id": profile_copy.get("product_id") if profile_copy else None,
            "capabilities": list(capabilities) if isinstance(capabilities, list) else [],
            "operations": dict(operations) if isinstance(operations, dict) else {},
        }

    def get_device_snapshots() -> tuple[dict[str, Any], ...]:
        """Return snapshots for all identified ESP32 devices."""
        return tuple(
            snapshot
            for device_id in tuple(data[DATA_DEVICES])
            if (snapshot := get_device_snapshot(device_id)) is not None
        )

    def register_remove_handler(integration: str, handler) -> Any:
        """Register an async cleanup callback for a specialized integration."""
        handlers = hass.data[DOMAIN][DATA_REMOVE_HANDLERS]
        handlers[integration] = handler

        def unsubscribe() -> None:
            if handlers.get(integration) is handler:
                handlers.pop(integration, None)

        return unsubscribe

    @callback
    def mqtt_connection_changed(connected: bool) -> None:
        """Mark all devices unavailable while the HA MQTT client is disconnected."""
        if connected:
            return
        for device_id in tuple(data[DATA_DEVICES]):
            hass.async_create_task(
                update_device(device_id, {
                    "available": False,
                    "availability_received": False,
                })
            )

    data[DATA_UNSUB] = [
        await mqtt.async_subscribe(hass, IDENTITY_TOPIC, identity_message, qos=1),
        await mqtt.async_subscribe(hass, PROFILE_TOPIC.replace("{device_id}", "+"), profile_message, qos=1),
        await mqtt.async_subscribe(hass, "esp32/+/availability", availability_message, qos=1),
        await mqtt.async_subscribe(hass, OTA_STATUS_TOPIC, status_message, qos=1),
        async_dispatcher_connect(hass, MQTT_CONNECTION_STATE, mqtt_connection_changed),
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
            sidebar_title="ESP32 Manager",
            sidebar_icon="mdi:chip",
            module_url=f"{PANEL_STATIC_URL}?v=2",
            require_admin=True,
        )
        hass.data[DOMAIN]["_panel_registered"] = True

    async def handle_update(call: ServiceCall) -> None:
        device_id = str(call.data["device_id"]).upper()
        url = str(call.data["url"]).strip()
        token = str(call.data.get("token", "")).strip()
        repo_id = str(call.data.get("repo_id", "")).strip()
        if device_id not in data[DATA_DEVICES]:
            raise HomeAssistantError("ESP32 не обнаружена. Проверьте MQTT и подключение устройства.")
        if not URL_RE.fullmatch(url):
            raise HomeAssistantError("Укажите прямой HTTPS URL файла прошивки с расширением .bin.")
        catalog_token = ""
        if repo_id:
            try:
                catalog_token = _get_repo(hass, repo_id).get("token", "")
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
        if token:
            await _save_token(hass, token)
        stored_token = token or catalog_token or await _read_token(hass)
        command = {"url": url}
        if stored_token:
            command["github_token"] = stored_token
        # Remove the previous firmware profile before starting OTA. The
        # empty retained payload is an MQTT tombstone; product integrations
        # must treat it as "profile unknown" until the new firmware reports.
        await mqtt.async_publish(
            hass, PROFILE_TOPIC.format(device_id=device_id),
            "", qos=1, retain=True
        )
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
        if device_registry.async_get_device(
            identifiers={(DEVICE_IDENTIFIER_NAMESPACE, device_id)}
        ) is None:
            raise HomeAssistantError("Устройство не найдено в реестре Home Assistant.")
        await _async_remove_device(hass, entry, data, device_id, remove_registry=True)

    hass.services.async_register(DOMAIN, "update", handle_update, schema=vol.Schema({
        vol.Required("device_id"): str, vol.Required("url"): str,
        vol.Optional("token", default=""): str,
        vol.Optional("repo_id"): str,
    }))
    device_schema = vol.Schema({vol.Required("device_id"): str})
    hass.services.async_register(DOMAIN, "restart", handle_restart, schema=device_schema)
    hass.services.async_register(DOMAIN, "factory_reset", handle_factory_reset, schema=device_schema)
    hass.services.async_register(DOMAIN, "save_token", handle_save_token,
                                 schema=vol.Schema({vol.Required("token"): str}))
    hass.services.async_register(
        DOMAIN, "remove_device", handle_remove_device, schema=device_schema
    )
    # Publish the API only after the Manager config entry has completed setup.
    hass.data[DOMAIN][DATA_API] = {
        "version": API_VERSION,
        "get_device": get_device_snapshot,
        "get_devices": get_device_snapshots,
        "register_remove_handler": register_remove_handler,
    }
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Coordinate consumer cleanup before Home Assistant removes an ESP32 device."""
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

    await _async_remove_device(
        hass, entry, data, device_id, remove_registry=False
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
    consumers = hass.data.get(DOMAIN, {}).get(DATA_REMOVE_HANDLERS, {})
    if consumers:
        _LOGGER.warning(
            "Cannot unload ESP32 Manager while dependent integrations are active: %s",
            ", ".join(sorted(consumers)),
        )
        return False

    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    if hass.data.get(DOMAIN, {}).get("_panel_registered"):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        hass.data[DOMAIN]["_panel_registered"] = False

    data = hass.data[DOMAIN].pop(entry.entry_id, {})
    hass.data[DOMAIN].pop(DATA_API, None)
    hass.data[DOMAIN].get(DATA_REMOVE_HANDLERS, {}).clear()
    for unsub in data.get(DATA_UNSUB, []):
        unsub()
    hass.services.async_remove(DOMAIN, "update")
    hass.services.async_remove(DOMAIN, "save_token")
    hass.services.async_remove(DOMAIN, "restart")
    hass.services.async_remove(DOMAIN, "factory_reset")
    hass.services.async_remove(DOMAIN, "remove_device")
    return True
