class Esp32OtaManagerPanel extends HTMLElement {
  constructor() {
    super();
    this._selected = "";
    this._deviceNames = new Map();
    this._deviceNamesUpdatedAt = 0;
    this._deviceNamesPromise = null;
    this._busy = false;
    this._actionMessage = "";
    this._controlMessage = "";
  }

  set hass(hass) {
    this._hass = hass;
    this._loadDeviceNames();
    this._render();
  }

  connectedCallback() {
    this._render();
    this._loadDeviceNames();
    this._timer = window.setInterval(() => this._loadDeviceNames(), 30000);
  }

  disconnectedCallback() {
    if (this._timer) window.clearInterval(this._timer);
  }

  async _loadDeviceNames() {
    if (!this._hass || this._deviceNamesPromise ||
        Date.now() - this._deviceNamesUpdatedAt < 30000) return;
    this._deviceNamesPromise = (async () => {
      try {
        const devices = await this._hass.connection.sendMessagePromise({
          type: "config/device_registry/list"
        });
        this._deviceNames = new Map();
        for (const device of devices) {
          const identifier = (device.identifiers || []).find(
            ([namespace]) => namespace === "esp32"
          );
          if (identifier) {
            const name = device.name_by_user || device.name;
            if (name) this._deviceNames.set(String(identifier[1]).toUpperCase(), name);
          }
        }
      } catch (error) {
        console.warn("ESP32 OTA Manager: unable to load device registry", error);
      } finally {
        this._deviceNamesUpdatedAt = Date.now();
        this._deviceNamesPromise = null;
        this._render();
      }
    })();
    return this._deviceNamesPromise;
  }

  _devices() {
    if (!this._hass) return [];
    const devices = new Map();
    for (const state of Object.values(this._hass.states)) {
      const attrs = state.attributes || {};
      const id = String(attrs.device_id || "").toUpperCase();
      if (!state.entity_id.startsWith("sensor.") || !/^[0-9A-F]{12}$/.test(id)) continue;
      devices.set(id, {
        id,
        name: this._deviceNames.get(id) || attrs.device_name || attrs.friendly_name || "ESP32",
        status: String(state.state || "unknown"),
        available: attrs.device_available === true,
        progress: Number.isFinite(Number(attrs.progress)) ? Math.max(0, Math.min(100, Number(attrs.progress))) : null,
        message: String(attrs.message || "")
      });
    }
    return [...devices.values()].sort((a, b) => a.name.localeCompare(b.name, "ru"));
  }

  _status(device) {
    if (!device) return {label:"Устройство не обнаружено", detail:"Ожидание MQTT identity от ESP32.", kind:"neutral"};
    if (!device.available) return {label:"Не в сети", detail:"Нет MQTT-соединения с устройством.", kind:"error"};
    const map = {
      online:["На связи","Устройство подключено к MQTT.","success"],
      downloading:["Загрузка прошивки","Получение файла прошивки по HTTPS.","active"],
      verifying:["Подготовка образа","Подготовка образа прошивки к установке.","active"],
      installing:["Установка прошивки","Запись образа в OTA-раздел.","active"],
      rebooting:["Перезагрузка","Устройство перезагружается после обновления.","active"],
      success:["Обновление успешно","Новая прошивка успешно запущена и проверена.","success"],
      error:["Ошибка обновления","Устройство сообщило об ошибке OTA.","error"]
    };
    const value = map[device.status];
    return value ? {label:value[0],detail:value[1],kind:value[2]} :
      {label:device.status === "Ожидание" ? "Ожидание команды" : device.status,
       detail:"Ожидание сообщения от устройства.",kind:"neutral"};
  }

  _render() {
    if (!this._hass) return;
    const devices = this._devices();
    if (!devices.some(device => device.id === this._selected)) this._selected = devices[0]?.id || "";
    const selected = devices.find(device => device.id === this._selected) || null;
    const status = this._status(selected);
    const previous = {};
    for (const id of ["url", "token"]) {
      const input = this.querySelector("#" + id);
      if (input) previous[id] = input.value;
    }
    const progressVisible = selected && ["downloading","verifying","installing","rebooting"].includes(selected.status);
    this.innerHTML = `
      <style>
        :host{display:block;padding:24px;color:var(--primary-text-color)}
        .content{max-width:760px;margin:0 auto}
        h1{font-size:24px;font-weight:500;margin:0 0 8px}
        .intro{color:var(--secondary-text-color);line-height:1.5;margin:0 0 20px}
        .card{background:var(--card-background-color);border-radius:12px;padding:20px;margin:0 0 16px;box-shadow:var(--ha-card-box-shadow,0 2px 8px #0002)}
        label{display:block;font-size:13px;color:var(--secondary-text-color);margin:14px 0 6px}
        input,select{box-sizing:border-box;width:100%;min-height:44px;padding:10px 12px;border:1px solid var(--outline-color,var(--divider-color));border-radius:8px;background:var(--input-fill-color,var(--card-background-color));color:var(--primary-text-color);font:inherit}
        .status{display:flex;gap:12px;align-items:center;padding:14px;border-radius:10px;background:var(--secondary-background-color);margin:14px 0}
        .status.success{border-inline-start:4px solid var(--success-color)}
        .status.error{border-inline-start:4px solid var(--error-color)}
        .status.active{border-inline-start:4px solid var(--info-color,var(--primary-color))}
        .status-title{font-weight:600}.detail,.hint{color:var(--secondary-text-color);font-size:13px;margin-top:4px;line-height:1.45}
        .progress{height:8px;background:var(--divider-color);border-radius:8px;overflow:hidden;margin-top:10px}
        .progress>div{height:100%;background:var(--primary-color);transition:width .2s}
        .actions{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:16px}
        button.full-width{grid-column:1/-1}
        button{min-height:44px;border:0;border-radius:8px;padding:10px 14px;font:inherit;font-weight:600;cursor:pointer;background:var(--primary-color);color:var(--text-primary-color,white)}
        button.secondary{background:var(--secondary-background-color);color:var(--primary-text-color)}
        button.primary{background:var(--primary-color);color:var(--text-primary-color,white)}
        button.danger{background:var(--error-color);color:white}
        button:disabled{opacity:.45;cursor:not-allowed}
        .message{min-height:20px;margin-top:12px;font-size:13px;overflow-wrap:anywhere;color:var(--secondary-text-color)}
        .id{font-size:12px;color:var(--secondary-text-color);margin-top:8px}
        @media(max-width:600px){:host{padding:16px}.card{padding:16px}}
        @media(max-width:380px){.actions{grid-template-columns:1fr}}
      </style>
      <div class="content">
        <h1>ESP32 OTA Manager</h1>
        <p class="intro">Обновление прошивки и управление ESP32-шлюзами.</p>
        <section class="card">
          <label for="device">Устройство</label>
          <select id="device" ${devices.length ? "" : "disabled"}>
            ${devices.map(device => `<option value="${this._escape(device.id)}" ${device.id===this._selected?"selected":""}>${this._escape(device.name)} · ${this._escape(device.id)}</option>`).join("")}
          </select>
          <div class="status ${status.kind}">
            <div><div class="status-title">${this._escape(status.label)}</div><div class="detail">${this._escape(status.detail)}</div>
            ${progressVisible && selected.progress !== null ? `<div class="progress"><div style="width:${selected.progress}%"></div></div><div class="detail">${selected.progress}%</div>` : ""}</div>
          </div>
          ${selected?.message ? `<div class="hint">${this._escape(selected.message)}</div>` : ""}
          ${selected ? `<div class="id">ID: ${this._escape(selected.id)}</div>` : ""}
          <label for="url">URL файла прошивки (.bin)</label>
          <input id="url" type="url" placeholder="https://github.com/.../firmware.bin">
          <label for="token">GitHub token (необязательно)</label>
          <input id="token" type="password" autocomplete="new-password" placeholder="Пустое поле — использовать сохранённый токен">
          <div class="actions">
            <button id="update" class="danger" ${selected?.available && !this._busy ? "" : "disabled"}>Начать обновление</button>
            <button id="save" class="primary" ${!this._busy ? "" : "disabled"}>Сохранить токен</button>
          </div>
          <div id="action-status" class="message" role="status" aria-live="polite">${this._escape(this._actionMessage || (devices.length ? "Готово к отправке OTA-команды." : "Ожидание обнаружения ESP32 по MQTT…"))}</div>
        </section>
        <section class="card">
          <h2 style="font-size:18px;font-weight:500;margin:0">Управление устройством</h2>
          <div class="actions">
            <button id="factory-reset" class="danger" ${selected?.available && !this._busy ? "" : "disabled"}>Полный сброс</button>
            <button id="restart" class="primary" ${selected?.available && !this._busy ? "" : "disabled"}>Перезагрузка</button>
            <button id="remove-device" class="danger full-width" ${selected && !this._busy ? "" : "disabled"}>Удалить из Home Assistant</button>
          </div>
          <div class="hint">Полный сброс удаляет пользовательские настройки в соответствии с реализацией прошивки. Успешная отправка команды не подтверждает завершение операции.</div>
          <div id="control-status" class="message" role="status" aria-live="polite">${this._escape(this._controlMessage)}</div>
        </section>
      </div>`;
    for (const id of ["url","token"]) if (previous[id] !== undefined && this.querySelector("#"+id)) this.querySelector("#"+id).value=previous[id];
    this.querySelector("#device")?.addEventListener("change", event => {this._selected=event.target.value;this._render();});
    this.querySelector("#save")?.addEventListener("click", () => this._saveToken());
    this.querySelector("#update")?.addEventListener("click", () => this._update());
    this.querySelector("#restart")?.addEventListener("click", () => this._deviceCommand("restart"));
    this.querySelector("#factory-reset")?.addEventListener("click", () => this._deviceCommand("factory_reset"));
    this.querySelector("#remove-device")?.addEventListener("click", () => this._removeDevice());
  }

  async _saveToken() {
    const token=this.querySelector("#token")?.value || "";
    if(!token){this._message("action-status","Введите GitHub token для сохранения.");return;}
    await this._run(async()=>{await this._hass.callService("esp32_ota","save_token",{token});this.querySelector("#token").value="";this._message("action-status","GitHub token сохранён в secrets.yaml.");});
  }

  async _update() {
    const url=(this.querySelector("#url")?.value || "").trim();
    const token=this.querySelector("#token")?.value || "";
    if(!this._selected || !url){this._message("action-status","Выберите устройство и заполните URL файла прошивки.");return;}
    await this._run(async()=>{await this._hass.callService("esp32_ota","update",{device_id:this._selected,url,token});this.querySelector("#token").value="";this._message("action-status","Команда отправлена. Ожидаем статус от ESP32…");});
  }

  async _removeDevice() {
    const device=this._devices().find(item=>item.id===this._selected);
    if(!device) return;
    if (!window.confirm(`Полностью удалить «${device.name}» из Home Assistant? Будут удалены запись устройства и связанные с ней сущности всех интеграций.`)) return;
    if (!window.confirm("Подтвердите удаление. Если ESP32 продолжит передавать MQTT identity, Home Assistant обнаружит её снова.")) return;
    await this._run(async()=>{
      await this._hass.callService("esp32_ota","remove_device",{device_id:device.id});
      this._selected="";
      this._deviceNames.delete(device.id);
      this._deviceNamesUpdatedAt=0;
      this._message("control-status","Устройство удалено из Home Assistant. Повторное обнаружение по MQTT разрешено.");
    },"control-status");
  }

  async _deviceCommand(command) {
    const device=this._devices().find(item=>item.id===this._selected);
    if(!device) return;
    if(command==="restart" && !window.confirm(`Перезагрузить устройство «${device.name}»?`)) return;
    if(command==="factory_reset") {
      if(!window.confirm(`ВНИМАНИЕ! Выполнить полный сброс «${device.name}»? Пользовательские настройки будут удалены. Отменить действие нельзя.`)) return;
      if(!window.confirm("Подтвердите полный сброс ещё раз.")) return;
    }
    await this._run(async()=>{await this._hass.callService("esp32_ota",command,{device_id:device.id});this._message("control-status",command==="restart"?"Команда перезагрузки отправлена в MQTT.":"Команда полного сброса отправлена в MQTT.");},"control-status");
  }

  async _run(action, target = "action-status") {
    if (this._busy) return;
    this._busy = true;
    this._render();
    try {
      await action();
    } catch (error) {
      this._message(target, "Ошибка: " + error);
    } finally {
      this._busy = false;
      this._render();
    }
  }

  _message(id, text) {
    if (id === "control-status") this._controlMessage = text;
    else this._actionMessage = text;
    const element = this.querySelector("#" + id);
    if (element) element.textContent = text;
  }

  _escape(value){return String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));}
}

customElements.define("esp32-ota-manager-panel", Esp32OtaManagerPanel);
