"""Sensors representing discovered ESP32 devices."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    data["add_entities"] = async_add_entities
    entities = []
    for device_id in data["devices"]:
        data["entity_ids"].add(device_id)
        entities.append(Esp32OtaSensor(hass, entry, device_id))
    if entities:
        async_add_entities(entities)


class Esp32OtaSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "OTA status"
    _attr_icon = "mdi:chip"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device_id: str) -> None:
        self.hass = hass
        self.entry = entry
        self.device_id = device_id
        self._attr_unique_id = f"{device_id.lower()}_ota_status"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self.entry.entry_id}_update", self._handle_update
        ))

    def _handle_update(self, device_id: str) -> None:
        if device_id == self.device_id:
            self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        device = self.hass.data[DOMAIN][self.entry.entry_id]["devices"].get(self.device_id, {})
        return DeviceInfo(
            identifiers={(DOMAIN, self.device_id)},
            name=device.get("name", f"ESP32-{self.device_id[-6:]}"),
            manufacturer="Espressif",
            model=device.get("model", "ESP32"),
        )

    @property
    def extra_state_attributes(self) -> dict:
        device = self.hass.data[DOMAIN][self.entry.entry_id]["devices"].get(self.device_id, {})
        return {
            "device_id": self.device_id,
            "device_name": device.get("name"),
            "device_available": device.get("available", False),
            "progress": device.get("progress"),
            "message": device.get("message"),
        }

    @property
    def native_value(self) -> str:
        device = self.hass.data[DOMAIN][self.entry.entry_id]["devices"].get(self.device_id, {})
        return device.get("ota_status", "Ожидание")
