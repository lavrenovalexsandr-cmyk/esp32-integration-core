DOMAIN = "esp32_ota"
DEVICE_IDENTIFIER_NAMESPACE = "esp32"
PLATFORMS = ["sensor"]
IDENTITY_TOPIC = "esp32/+/device/info"
OTA_STATUS_TOPIC = "esp32/+/ota/status"
OTA_COMMAND_TOPIC = "esp32/{device_id}/ota/update"
PROFILE_TOPIC = "esp32/{device_id}/device/profile"
TOKEN_SECRET_KEY = "esp32_ota_github_token"

PANEL_URL_PATH = "esp32-ota-manager"
PANEL_WEB_COMPONENT = "esp32-ota-manager-panel"
PANEL_STATIC_URL = "/esp32_ota/ota-panel.js"


# Public API shared with specialized ESP32 integrations.
API_VERSION = 1
DATA_API = "api"
DATA_REMOVE_HANDLERS = "remove_handlers"
EVENT_DEVICE_ADDED = "esp32_ota_device_added"
EVENT_DEVICE_UPDATED = "esp32_ota_device_updated"
