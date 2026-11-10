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
from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, IDENTITY_TOPIC, OTA_COMMAND_TOPIC, OTA_STATUS_TOPIC, PLATFORMS, TOKEN_SECRET_KEY

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
    }
    signal = f"{DOMAIN}_{entry.entry_id}_update"

    async def update_device(device_id: str, values: dict[str, Any]) -> None:
        device = data[DATA_DEVICES].setdefault(device_id, {
            "id": device_id, "name": f"ESP32-{device_id[-6:]}", "model": "ESP32"
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
            await update_device(device_id, {"name": name[:64], "model": model[:64], "available": True})
        except (ValueError, TypeError, AttributeError):
            _LOGGER.warning("Ignoring invalid ESP32 identity message")

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
        await mqtt.async_subscribe(hass, OTA_STATUS_TOPIC, status_message, qos=1),
    ]
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_update(call: ServiceCall) -> None:
        device_id = str(call.data["device_id"]).upper()
        url = str(call.data["url"]).strip()
        checksum = str(call.data["sha256"]).lower().strip()
        token = str(call.data.get("token", "")).strip()
        if device_id not in data[DATA_DEVICES]:
            raise HomeAssistantError("ESP32 не обнаружена. Проверьте MQTT и подключение устройства.")
        if not URL_RE.fullmatch(url):
            raise HomeAssistantError("Укажите прямой HTTPS URL файла прошивки с расширением .bin.")
        if not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise HomeAssistantError("Для OTA обязателен SHA-256 из 64 шестнадцатеричных символов.")
        if token:
            await _save_token(hass, token)
        stored_token = token or await _read_token(hass)
        command = {"url": url, "sha256": checksum}
        if stored_token:
            command["github_token"] = stored_token
        await mqtt.async_publish(
            hass, OTA_COMMAND_TOPIC.format(device_id=device_id),
            json.dumps(command, separators=(",", ":")), qos=1, retain=False
        )

    async def handle_save_token(call: ServiceCall) -> None:
        await _save_token(hass, str(call.data.get("token", "")).strip())

    hass.services.async_register(DOMAIN, "update", handle_update, schema=vol.Schema({
        vol.Required("device_id"): str, vol.Required("url"): str, vol.Required("sha256"): str,
        vol.Optional("token", default=""): str,
    }))
    hass.services.async_register(DOMAIN, "save_token", handle_save_token,
                                 schema=vol.Schema({vol.Required("token"): str}))
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
    data = hass.data[DOMAIN].pop(entry.entry_id, {})
    for unsub in data.get(DATA_UNSUB, []):
        unsub()
    await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    hass.services.async_remove(DOMAIN, "update")
    hass.services.async_remove(DOMAIN, "save_token")
    return True
