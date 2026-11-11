class Esp32DeviceControlCard extends HTMLElement {
  setConfig(config) {
    this.config = { title: "Управление ESP32", ...config };
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  getDevices() {
    if (!this._hass) return [];
    const found = new Map();
    Object.values(this._hass.states).forEach(state => {
      const id = state.attributes && state.attributes.device_id;
      if (state.entity_id.startsWith("sensor.") && id) {
        found.set(String(id).toUpperCase(), {
          id: String(id).toUpperCase(),
          name: state.attributes.device_name || state.attributes.friendly_name || "ESP32"
        });
      }
    });
    return [...found.values()].sort((a, b) => a.name.localeCompare(b.name, "ru"));
  }

  render() {
    if (!this._hass || !this.config) return;
    const devices = this.getDevices();
    const selected = this._selected || (devices[0] && devices[0].id) || "";
    this._selected = selected;
    this.innerHTML = `
      <ha-card header="${this.escape(this.config.title)}">
        <style>
          .control-content { padding: 16px; }
          .control-label { display:block; color:var(--secondary-text-color); font-size:13px; margin:0 0 7px; }
          .control-select { box-sizing:border-box; width:100%; padding:12px; border:1px solid var(--divider-color); border-radius:8px; background:var(--card-background-color); color:var(--primary-text-color); font:inherit; }
          .control-actions { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:18px; }
          .control-actions button { min-height:48px; border:0; border-radius:9px; padding:10px; font:inherit; font-weight:600; cursor:pointer; }
          .reset { background:var(--error-color); color:white; }
          .restart { background:var(--primary-color); color:var(--text-primary-color, white); }
          button:disabled, select:disabled { opacity:.45; cursor:not-allowed; }
          .control-hint { margin-top:12px; color:var(--secondary-text-color); font-size:12px; line-height:1.45; }
          @media(max-width:380px) { .control-actions { grid-template-columns:1fr; } }
        </style>
        <div class="control-content">
          <label class="control-label" for="esp-device">Устройство</label>
          <select id="esp-device" class="control-select" ${devices.length ? "" : "disabled"}>
            ${devices.map(d => `<option value="${this.escape(d.id)}" ${d.id === selected ? "selected" : ""}>${this.escape(d.name)} · ${this.escape(d.id)}</option>`).join("")}
          </select>
          <div class="control-actions">
            <button class="reset" id="factory-reset" ${devices.length ? "" : "disabled"}>Полный сброс</button>
            <button class="restart" id="restart" ${devices.length ? "" : "disabled"}>Перезагрузка</button>
          </div>
          <div class="control-hint" id="control-status">${devices.length ? "Выберите ESP32 и действие." : "Устройства пока не обнаружены. Проверьте MQTT-подключение."}</div>
        </div>
      </ha-card>`;

    this.querySelector("#esp-device").addEventListener("change", e => {
      this._selected = e.target.value;
    });
    this.querySelector("#restart").addEventListener("click", async () => {
      const device = devices.find(d => d.id === this.querySelector("#esp-device").value);
      if (!device || !window.confirm(`Перезагрузить устройство «${device.name}»?`)) return;
      await this.sendCommand("restart", device);
    });
    this.querySelector("#factory-reset").addEventListener("click", async () => {
      const device = devices.find(d => d.id === this.querySelector("#esp-device").value);
      if (!device || !window.confirm(`ВНИМАНИЕ! Полностью сбросить настройки «${device.name}»? Wi-Fi и привязки BLE будут удалены. Отменить это действие нельзя.`)) return;
      if (!window.confirm("Подтвердите полный сброс ещё раз.")) return;
      await this.sendCommand("factory_reset", device);
    });
  }

  async sendCommand(service, device) {
    const status = this.querySelector("#control-status");
    status.textContent = "Отправка команды…";
    try {
      await this._hass.callService("esp32_ota", service, { device_id: device.id });
      status.textContent = service === "restart"
        ? "Команда перезагрузки отправлена."
        : "Команда полного сброса отправлена.";
    } catch (error) {
      status.textContent = "Не удалось отправить команду: " + error;
    }
  }

  escape(value) {
    return String(value).replace(/[&<>"']/g, ch => ({
      "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"
    }[ch]));
  }

  getCardSize() { return 3; }
}

customElements.define("esp32-device-control-card", Esp32DeviceControlCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "esp32-device-control-card",
  name: "ESP32 Device Control",
  description: "Отдельная карточка для перезагрузки и полного сброса ESP32"
});
