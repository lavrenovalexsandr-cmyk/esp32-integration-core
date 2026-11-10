DOMAIN = "esp32_ota"
PLATFORMS = ["sensor"]
IDENTITY_TOPIC = "esp32/+/device/info"
OTA_STATUS_TOPIC = "esp32/+/ota/status"
OTA_COMMAND_TOPIC = "esp32/{device_id}/ota/update"
TOKEN_SECRET_KEY = "esp32_ota_github_token"
