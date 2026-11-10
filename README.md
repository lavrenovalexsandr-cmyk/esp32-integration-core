[Русский](README.md) | [English](README_EN.md)

# ESP32 OTA Manager для Home Assistant

Пользовательская интеграция Home Assistant и Lovelace-карточка для обнаружения ESP32-устройств через MQTT, отображения их состояния и управления обновлением прошивки по OTA.

## Содержимое репозитория

- `custom_components/esp32_ota` — интеграция Home Assistant.
- `www/esp32-ota-card.js` — Lovelace-карточка для OTA-обновлений и отображения текущего состояния устройств.

## Требования

- Home Assistant с настроенной и подключённой официальной интеграцией MQTT.
- Прошивка ESP32, реализующая соответствующие MQTT-топики идентификации устройства, команды OTA-обновления и статуса обновления.

## Установка

1. Скопируйте каталог `custom_components/esp32_ota` в `<HA config>/custom_components/esp32_ota`.
2. Перезапустите Home Assistant.
3. Откройте **Настройки → Устройства и службы → Добавить интеграцию** и добавьте **ESP32 OTA Manager**.
4. Скопируйте файл `www/esp32-ota-card.js` в `<HA config>/www/esp32-ota-card.js`.
5. Добавьте ресурс Lovelace `/local/esp32-ota-card.js`, указав тип JavaScript-модуль.
6. Добавьте Lovelace-карточку вручную:

```yaml
type: custom:esp32-ota-card
title: ESP32 firmware updates
```

## MQTT-протокол

Интеграция обнаруживает устройства по retained-сообщениям с их идентификацией:

- Топик: `esp32/<12-uppercase-hex-id>/device/info`
- Пример содержимого:

```json
{"id":"AABBCCDDEEFF","name":"Kitchen","model":"ESP32-C3"}
```

Команда OTA-обновления (QoS 1, без retained):

- Топик: `esp32/<id>/ota/update`
- Содержимое:

```json
{"url":"https://.../firmware.bin","sha256":"<64 hex>","github_token":"<optional>"}
```

Статус OTA-обновления:

- Топик: `esp32/<id>/ota/status`
- Пример содержимого:

```json
{"status":"online|downloading|verifying|installing|rebooting|success|error","progress":0,"message":"..."}
```

Принимающая часть OTA на стороне прошивки поддерживается в отдельном проекте ESP32: [esp32_mqtt_xiaomi_lywsd03](https://github.com/lavrenovalexsandr-cmyk/esp32_mqtt_xiaomi_lywsd03). Прошивка загружает файл по HTTPS, проверяет SHA-256, записывает его в неактивный OTA-раздел и передаёт состояние обновления через MQTT.

## GitHub-токен для приватных релизов

Карточка позволяет указать необязательный GitHub-токен. Интеграция может сохранить его в `secrets.yaml` Home Assistant под именем `esp32_ota_github_token`.

При сохранении существующие строки и комментарии файла не удаляются. Файл записывается атомарно с правами доступа только для владельца. Токен, введённый непосредственно в форме обновления, имеет приоритет над сохранённым токеном для этого запроса.

Токен передаётся только API-адресам GitHub. Его нельзя пересылать на внешние CDN-серверы, с которых загружается файл прошивки.

## Проверка целостности прошивки

Всегда указывайте SHA-256 именно того бинарного файла прошивки, который собираетесь установить. Прошивка ESP32 должна проверить контрольную сумму до активации нового OTA-раздела.
