(() => {
  if (window.__DASHBOARD_LOADED__) {
    console.warn('dashboard.js ya estaba cargado, segunda carga ignorada');
    return;
  }

  window.__DASHBOARD_LOADED__ = true;

  const API_URL = '/api/sensors';
  const REFRESH_MS = 30_000;
  const HISTORY_MINUTES = 120;
  const HISTORY_AVG_SECONDS = 300;
  const METRICS_HISTORY_POINTS = 24;

  const USER_ROLE = String(document.body?.dataset.userRole || 'lector').trim().toLowerCase();
  const USERNAME = String(document.body?.dataset.username || 'default').trim().toLowerCase() || 'default';
  const METRICS_UI_ENABLED = document.body?.dataset.metricsUiEnabled === 'true';
  const METRICS_HISTORY = window.CoreViewMetricsHistory || null;
  const metricsHistoryClient = METRICS_UI_ENABLED && METRICS_HISTORY
    ? METRICS_HISTORY.createClient()
    : null;
  const ALERT_SETTINGS_KEY = `prtg_alert_settings_${USERNAME}`;

  const ADMIN_ROLES = new Set(['admin', 'admon']);
  const ADMON_ROLES = new Set(['admon']);

  const ADMIN_BLOCK_MESSAGE = 'no eres admin papito';
  const ADMON_BLOCK_MESSAGE = 'solo admon puede hacer eso papito';

  window.toastr = window.toastr || {
    success() {},
    error() {},
    warning() {},
    info() {}
  };

  toastr.options = {
    closeButton: true,
    positionClass: 'toast-bottom-right',
    timeOut: 3500,
    preventDuplicates: true
  };

  let sensorData = null;
  let refreshInterval = null;
  let currentView = 'sensors';

  let statusChart = null;
  let metricsChart = null;
  let historyChart = null;
  const normalizedHistoryCharts = [];
  let historyRefreshInterval = null;
  let activeHistoryRequest = null;
  let historyRenderVersion = 0;

  let lastDownFingerprint = '';
  let audioUnlocked = false;
  let activePauseRequest = null;
  let pinnedSensorKeys = new Set();
  let mfTimerInterval = null;

  const $ = (id) => document.getElementById(id);

  const statusConfig = {
    Up: {
      icon: 'fa-check-circle',
      textColor: 'text-prtg-up',
      borderColor: 'border-prtg-up',
      bgColor: 'bg-green-100'
    },
    Down: {
      icon: 'fa-times-circle',
      textColor: 'text-prtg-down',
      borderColor: 'border-prtg-down',
      bgColor: 'bg-red-100'
    },
    'Down Partial': {
      icon: 'fa-exclamation-circle',
      textColor: 'text-prtg-down',
      borderColor: 'border-prtg-down',
      bgColor: 'bg-red-100'
    },
    'Down Acknowledged': {
      icon: 'fa-user-check',
      textColor: 'text-prtg-acknowledged',
      borderColor: 'border-prtg-acknowledged',
      bgColor: 'bg-purple-100'
    },
    Warning: {
      icon: 'fa-exclamation-triangle',
      textColor: 'text-prtg-warning',
      borderColor: 'border-prtg-warning',
      bgColor: 'bg-yellow-100'
    },
    'Paused by Schedule': {
      icon: 'fa-pause-circle',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
    'Paused by Dependency': {
      icon: 'fa-pause-circle',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
    'Paused Until': {
      icon: 'fa-calendar-alt',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
    Unknown: {
      icon: 'fa-question-circle',
      textColor: 'text-prtg-unknown',
      borderColor: 'border-prtg-unknown',
      bgColor: 'bg-gray-100'
    },
    Unusual: {
      icon: 'fa-bolt',
      textColor: 'text-prtg-unknown',
      borderColor: 'border-prtg-unknown',
      bgColor: 'bg-gray-100'
    },
    Scanning: {
      icon: 'fa-spinner fa-spin',
      textColor: 'text-blue-500',
      borderColor: 'border-blue-500',
      bgColor: 'bg-blue-100'
    },
    None: {
      icon: 'fa-question-circle',
      textColor: 'text-prtg-unknown',
      borderColor: 'border-prtg-unknown',
      bgColor: 'bg-gray-100'
    },
    default: {
      icon: 'fa-question-circle',
      textColor: 'text-prtg-unknown',
      borderColor: 'border-prtg-unknown',
      bgColor: 'bg-gray-100'
    },
    Paused: {
      icon: 'fa-pause-circle',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
    'Paused by User': {
      icon: 'fa-pause-circle',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
    'Pausado': {
      icon: 'fa-pause-circle',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
    'Pausado por usuario': {
      icon: 'fa-pause-circle',
      textColor: 'text-prtg-paused',
      borderColor: 'border-prtg-paused',
      bgColor: 'bg-sky-100'
    },
  };

  const statusCenterTextPlugin = {
    id: 'statusCenterTextPlugin',
    afterDraw(chart) {
      if (chart.config.type !== 'doughnut') return;

      const { ctx, chartArea } = chart;
      if (!chartArea) return;

      const total = chart.config.options.plugins?.centerText?.total ?? 0;
      const x = (chartArea.left + chartArea.right) / 2;
      const y = (chartArea.top + chartArea.bottom) / 2;

      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = '#f4f4f5';
      ctx.font = '800 30px Inter, sans-serif';
      ctx.fillText(String(total), x, y - 2);

      ctx.fillStyle = '#71717a';
      ctx.font = '600 11px Inter, sans-serif';
      ctx.fillText('sensores', x, y + 22);
      ctx.restore();
    }
  };

  if (typeof Chart !== 'undefined') {
    Chart.register(statusCenterTextPlugin);
  }

  function escapeHTML(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (char) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[char]));
  }

  function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }

  async function fetchJSON(url, retries = 2) {
    for (let attempt = 0; attempt <= retries; attempt += 1) {
      try {
        const response = await fetch(url, {
          cache: 'no-store',
          credentials: 'same-origin'
        });

        const payload = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(payload.error || `HTTP ${response.status}`);
        }

        return payload;
      } catch (error) {
        if (attempt === retries) throw error;
        await sleep(500 * Math.pow(2, attempt));
      }
    }

    throw new Error('No se pudo completar la solicitud.');
  }

  async function requestJSON(url, { method = 'GET', body = null } = {}) {
    const options = {
      method,
      credentials: 'same-origin',
      cache: 'no-store',
      headers: {
        Accept: 'application/json'
      }
    };

    if (body !== null) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }

    return payload;
  }

  function formatTimestamp(ts) {
    if (!ts) return null;

    try {
      return new Date(ts).toLocaleString('es-MX', {
        dateStyle: 'short',
        timeStyle: 'medium'
      });
    } catch {
      return ts;
    }
  }

  function showLoading() {
    $('loading-state')?.classList.remove('hidden');
    $('view-content')?.classList.add('hidden');
    $('error-state')?.classList.add('hidden');
  }

  function hideLoading() {
    $('loading-state')?.classList.add('hidden');
    $('view-content')?.classList.remove('hidden');
    $('error-state')?.classList.add('hidden');
  }

  function showError(message) {
    $('loading-state')?.classList.add('hidden');
    $('view-content')?.classList.add('hidden');

    if ($('error-message')) {
      $('error-message').textContent = message;
    }

    $('error-state')?.classList.remove('hidden');
    toastr.error(message, 'Error');
  }


  function isAdmin() {
    return ADMIN_ROLES.has(USER_ROLE);
  }

  function isAdmon() {
    return ADMON_ROLES.has(USER_ROLE);
  }

  function requireAdmin() {
    if (isAdmin()) return true;

    toastr.warning(ADMIN_BLOCK_MESSAGE, 'Permiso denegado');
    return false;
  }

  function requireAdmon() {
    if (isAdmon()) return true;

    toastr.warning(ADMON_BLOCK_MESSAGE, 'Permiso denegado');
    return false;
  }

  function toggleSettingsMenu(force) {
    const menu = $('settings-menu');
    const fab = $('settings-fab');

    if (!menu || !fab) return;

    const shouldOpen = typeof force === 'boolean'
      ? force
      : menu.classList.contains('hidden');

    menu.classList.toggle('hidden', !shouldOpen);
    fab.classList.toggle('open', shouldOpen);
  }

  function openModal(id) {
    const modal = $(id);
    if (!modal) return;

    modal.classList.remove('hidden');
    modal.classList.add('flex');
  }

  function closeModal(id) {
    const modal = $(id);
    if (!modal) return;

    modal.classList.add('hidden');
    modal.classList.remove('flex');
  }

  function setModalMessage(id, message, type = 'info') {
    const box = $(id);
    if (!box) return;

    if (!message) {
      box.classList.add('hidden');
      box.textContent = '';
      return;
    }

    box.className = 'rounded-lg p-3 text-sm mb-4';

    const classes = type === 'error'
      ? ['bg-red-50', 'text-red-700', 'border', 'border-red-200']
      : type === 'success'
        ? ['bg-green-50', 'text-green-700', 'border', 'border-green-200']
        : ['bg-blue-50', 'text-blue-700', 'border', 'border-blue-200'];

    box.classList.add(...classes);
    box.textContent = message;
  }

  function roleBadgeClass(role) {
    const normalized = String(role || 'lector').toLowerCase();

    if (normalized === 'admon') {
      return 'bg-orange-100 text-orange-700';
    }

    if (normalized === 'admin') {
      return 'bg-purple-100 text-purple-700';
    }

    return 'bg-gray-100 text-gray-600';
  }

  function activeBadgeClass(isActive) {
    return Number(isActive) === 1
      ? 'bg-green-100 text-green-700'
      : 'bg-red-100 text-red-700';
  }

  function defaultAlertSettings() {
    return {
      enabled: true,
      repeatWhileDown: false,
      volume: 75,
      soundFile: null,
      soundUrl: null
    };
  }

  function normalizeAlertSettings(settings = {}) {
    return {
      ...defaultAlertSettings(),
      ...settings,
      enabled: Boolean(settings.enabled),
      repeatWhileDown: Boolean(settings.repeatWhileDown),
      volume: Math.max(0, Math.min(100, Number(settings.volume ?? 75))),
      soundFile: settings.soundFile || null,
      soundUrl: settings.soundUrl || null
    };
  }

  function readLocalAlertSettings() {
    try {
      const raw = localStorage.getItem(ALERT_SETTINGS_KEY);
      return raw ? normalizeAlertSettings(JSON.parse(raw)) : defaultAlertSettings();
    } catch {
      return defaultAlertSettings();
    }
  }

  let alertSettingsCache = readLocalAlertSettings();

  function loadAlertSettings() {
    return normalizeAlertSettings(alertSettingsCache || readLocalAlertSettings());
  }

  function storeAlertSettings(settings) {
    alertSettingsCache = normalizeAlertSettings(settings);
    localStorage.setItem(ALERT_SETTINGS_KEY, JSON.stringify(alertSettingsCache));
  }

  async function fetchAlertSettingsFromServer() {
    try {
      const payload = await requestJSON('/api/me/alert-settings');
      storeAlertSettings(payload.settings || {});
      return loadAlertSettings();
    } catch (error) {
      console.warn('No se pudieron cargar alertas desde servidor. Uso localStorage.', error);
      return loadAlertSettings();
    }
  }

  async function saveAlertSettings(settings) {
    const clean = normalizeAlertSettings(settings);

    try {
      const payload = await requestJSON('/api/me/alert-settings', {
        method: 'PUT',
        body: {
          enabled: clean.enabled,
          repeatWhileDown: clean.repeatWhileDown,
          volume: clean.volume
        }
      });

      storeAlertSettings(payload.settings || clean);
      return loadAlertSettings();
    } catch (error) {
      storeAlertSettings(clean);
      throw error;
    }
  }

  function unlockAudio() {
    audioUnlocked = true;
  }

  async function playGeneratedAlertSound(options = {}) {
    const { force = false } = options;
    const settings = loadAlertSettings();

    if (!force && !settings.enabled) return;

    if (!audioUnlocked && !force) {
      console.info('Audio bloqueado hasta que el usuario interactúe con la página.');
      return;
    }

    try {
      audioUnlocked = true;

      const AudioContext = window.AudioContext || window.webkitAudioContext;

      if (!AudioContext) {
        console.warn('Este navegador no soporta Web Audio API.');
        return;
      }

      const ctx = new AudioContext();

      if (ctx.state === 'suspended') {
        await ctx.resume();
      }

      const volume = Math.max(0, Math.min(1, Number(settings.volume || 75) / 100));

      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0.0001, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(volume, ctx.currentTime + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.75);

      const osc1 = ctx.createOscillator();
      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(880, ctx.currentTime);
      osc1.frequency.setValueAtTime(660, ctx.currentTime + 0.18);
      osc1.frequency.setValueAtTime(740, ctx.currentTime + 0.36);

      const osc2 = ctx.createOscillator();
      osc2.type = 'triangle';
      osc2.frequency.setValueAtTime(440, ctx.currentTime);

      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(ctx.destination);

      osc1.start(ctx.currentTime);
      osc2.start(ctx.currentTime + 0.08);

      osc1.stop(ctx.currentTime + 0.75);
      osc2.stop(ctx.currentTime + 0.75);

      setTimeout(() => {
        ctx.close().catch(() => {});
      }, 1000);
    } catch (error) {
      console.warn('No se pudo reproducir alerta sonora:', error);
    }
  }

  async function playUploadedAlertSound(url, options = {}) {
    const { force = false } = options;
    const settings = loadAlertSettings();

    if (!force && !settings.enabled) return;

    if (!audioUnlocked && !force) {
      console.info('Audio bloqueado hasta que el usuario interactúe con la página.');
      return;
    }

    try {
      audioUnlocked = true;

      const audio = new Audio(url);
      audio.volume = Math.max(0, Math.min(1, Number(settings.volume || 75) / 100));
      audio.currentTime = 0;

      await audio.play();
    } catch (error) {
      console.warn('No se pudo reproducir MP3. Uso sonido default.', error);
      await playGeneratedAlertSound(options);
    }
  }

  async function playDefaultAlertSound(options = {}) {
    const settings = loadAlertSettings();

    if (settings.soundUrl) {
      await playUploadedAlertSound(settings.soundUrl, options);
      return;
    }

    await playGeneratedAlertSound(options);
  }

  function downSensorKey(sensor) {
    return [
      sensor.grupo || '',
      sensor.sensor_id || '',
      sensor.device || '',
      sensor.sensor || ''
    ].join('|');
  }

  function getDownSensors(data) {
    return (data?.sensores || []).filter(sensor =>
      ['Down', 'Down Partial'].includes(sensor.status)
    );
  }

  function getDownFingerprint(data) {
    return getDownSensors(data)
      .map(downSensorKey)
      .sort()
      .join('||');
  }

  function handleSoundAlerts(previousData, nextData) {
    const settings = loadAlertSettings();
    const currentDowns = getDownSensors(nextData);

    if (!settings.enabled) {
      lastDownFingerprint = getDownFingerprint(nextData);
      return;
    }

    if (!previousData) {
      lastDownFingerprint = getDownFingerprint(nextData);
      return;
    }

    if (settings.repeatWhileDown) {
      if (currentDowns.length > 0) {
        playDefaultAlertSound();
      }

      lastDownFingerprint = getDownFingerprint(nextData);
      return;
    }

    const previousDown = new Set(getDownSensors(previousData).map(downSensorKey));
    const newDowns = currentDowns.filter(sensor => !previousDown.has(downSensorKey(sensor)));

    lastDownFingerprint = getDownFingerprint(nextData);

    if (newDowns.length > 0) {
      playDefaultAlertSound();
    }
  }

  function updateAlertSoundLabel() {
    const settings = loadAlertSettings();
    const label = $('alert-current-sound');

    if (!label) return;

    if (settings.soundFile || settings.soundUrl) {
      const name = String(settings.soundFile || settings.soundUrl).split('/').pop();
      label.textContent = `Actual: ${name}`;
    } else {
      label.textContent = 'Actual: sonido default generado por navegador';
    }
  }

  async function loadAlertModalValues() {
    await fetchAlertSettingsFromServer();

    const settings = loadAlertSettings();

    if ($('alert-sound-enabled')) {
      $('alert-sound-enabled').checked = Boolean(settings.enabled);
    }

    if ($('alert-repeat-down')) {
      $('alert-repeat-down').checked = Boolean(settings.repeatWhileDown);
    }

    if ($('alert-volume')) {
      $('alert-volume').value = String(settings.volume ?? 75);
    }

    updateAlertSoundLabel();
  }

  async function saveAlertModalValues() {
    const settings = {
      ...loadAlertSettings(),
      enabled: Boolean($('alert-sound-enabled')?.checked),
      repeatWhileDown: Boolean($('alert-repeat-down')?.checked),
      volume: Number($('alert-volume')?.value || 75)
    };

    try {
      await saveAlertSettings(settings);

      setModalMessage('alerts-modal-message', 'Preferencias de alerta guardadas.', 'success');
      toastr.success('Alertas guardadas');
    } catch (error) {
      setModalMessage('alerts-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudieron guardar');
    }
  }

  async function uploadAlertSound() {
    const input = $('alert-sound-file');
    const file = input?.files?.[0];

    if (!file) {
      toastr.warning('Selecciona un MP3 primero');
      return;
    }

    if (!file.name.toLowerCase().endsWith('.mp3')) {
      toastr.warning('Solo se permiten archivos .mp3');
      return;
    }

    setModalMessage('alerts-modal-message', 'Subiendo MP3...');

    const formData = new FormData();
    formData.append('sound', file);

    try {
      const response = await fetch('/api/me/alert-sound', {
        method: 'POST',
        credentials: 'same-origin',
        body: formData
      });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      storeAlertSettings(payload.settings || {});
      updateAlertSoundLabel();

      if (input) input.value = '';

      setModalMessage('alerts-modal-message', payload.message || 'MP3 actualizado.', 'success');
      toastr.success('MP3 de alerta actualizado');
    } catch (error) {
      setModalMessage('alerts-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo subir');
    }
  }

  async function resetAlertSound() {
    const ok = confirm('¿Seguro que quieres volver al sonido default?');

    if (!ok) return;

    setModalMessage('alerts-modal-message', 'Restaurando sonido default...');

    try {
      const response = await fetch('/api/me/alert-sound', {
        method: 'DELETE',
        credentials: 'same-origin'
      });

      const payload = await response.json().catch(() => ({}));

      if (!response.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      storeAlertSettings(payload.settings || {});
      updateAlertSoundLabel();

      setModalMessage('alerts-modal-message', payload.message || 'Sonido default restaurado.', 'success');
      toastr.success('Sonido default activo');
    } catch (error) {
      setModalMessage('alerts-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo restaurar');
    }
  }

  async function openAlertsModal() {
    toggleSettingsMenu(false);
    openModal('alerts-modal');
    setModalMessage('alerts-modal-message', '');
    await loadAlertModalValues();
  }

  function renderServersList(servers = [], readonly = false) {
    const list = $('servers-list');
    if (!list) return;

    if (!servers.length) {
      list.innerHTML = `
        <div class="rounded-xl border border-dashed border-gray-300 p-6 text-center">
          <i class="fa-solid fa-server text-gray-300 text-3xl mb-3"></i>
          <p class="text-sm text-gray-500">No hay servidores configurados todavía.</p>
        </div>
      `;
      return;
    }

    list.innerHTML = servers.map(server => {
      const serverId = escapeHTML(server.id || '');
      const name = escapeHTML(server.name || 'Sin nombre');
      const url = escapeHTML(server.url || '');
      const username = escapeHTML(server.username || '');
      const type = escapeHTML(server.type || 'prtg');
      const authMode = escapeHTML(server.auth_mode || 'password');
      const enabled = Boolean(server.enabled);

      return `
        <article class="admin-list-card server-admin-card">
          <div class="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_180px] gap-4 items-start">
            <div class="min-w-0">
              <div class="flex flex-wrap items-center gap-2 mb-2">
                <h5 class="font-bold text-gray-800 dark:text-gray-100 truncate">${name}</h5>

                <span class="text-[10px] uppercase rounded-full px-2 py-1 bg-blue-100 text-blue-700">
                  ${type}
                </span>

                <span class="text-[10px] uppercase rounded-full px-2 py-1 ${enabled ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'}">
                  ${enabled ? 'Activo' : 'Inactivo'}
                </span>
              </div>

              <p class="mono text-xs text-gray-500 break-all">
                <i class="fa-solid fa-link mr-1 text-gray-400"></i>
                ${url}
              </p>

              <p class="text-xs text-gray-500 mt-2">
                <i class="fa-solid fa-user mr-1 text-gray-400"></i>
                ${username}
              </p>

              <p class="text-xs text-gray-400 mt-1">
                Auth: ${authMode}
              </p>
            </div>

            ${readonly ? `
              <div class="rounded-xl bg-yellow-50 border border-yellow-200 p-3 text-xs text-yellow-800">
                Solo lectura: viene de PRTG_SERVERS_JSON.
              </div>
            ` : `
              <div class="flex flex-col gap-2">
                ${isAdmin() ? `
                  <button
                    type="button"
                    class="toggle-server-btn rounded-lg px-3 py-2 text-xs font-semibold ${enabled ? 'bg-yellow-50 text-yellow-700 hover:bg-yellow-100' : 'bg-green-50 text-green-700 hover:bg-green-100'}"
                    data-server-id="${serverId}"
                    data-enabled="${enabled ? '0' : '1'}"
                    data-server-name="${name}">
                    <i class="fa-solid ${enabled ? 'fa-power-off' : 'fa-circle-play'} mr-1"></i>
                    ${enabled ? 'Desactivar' : 'Activar'}
                  </button>
                ` : ''}

                ${isAdmon() ? `
                  <button
                    type="button"
                    class="delete-server-btn rounded-lg px-3 py-2 text-xs font-semibold bg-red-50 text-red-600 hover:bg-red-100"
                    data-server-id="${serverId}"
                    data-server-name="${name}">
                    <i class="fa-solid fa-trash-can mr-1"></i>
                    Borrar definitivo
                  </button>
                ` : ''}
              </div>
            `}
          </div>
        </article>
      `;
    }).join('');
  }

  async function loadAdminServers() {
    setModalMessage('server-modal-message', 'Cargando servidores...');

    try {
      const payload = await requestJSON('/api/admin/servers');

      renderServersList(payload.servers || [], Boolean(payload.readonly));

      setModalMessage(
        'server-modal-message',
        payload.message || '',
        payload.readonly ? 'info' : 'success'
      );
    } catch (error) {
      renderServersList([]);
      setModalMessage('server-modal-message', error.message, 'error');
    }
  }

  function openServerModal() {
    if (!requireAdmin()) return;

    toggleSettingsMenu(false);
    openModal('server-modal');
    setModalMessage('server-modal-message', '');
    loadAdminServers();
  }

  async function submitServerForm(event) {
    event.preventDefault();

    if (!requireAdmin()) return;

    const form = event.currentTarget;
    const data = new FormData(form);

    const payload = {
      name: String(data.get('name') || '').trim(),
      url: String(data.get('url') || '').trim(),
      username: String(data.get('username') || '').trim(),
      password: String(data.get('password') || '')
    };

    setModalMessage('server-modal-message', 'Guardando servidor...');

    try {
      await requestJSON('/api/admin/servers', {
        method: 'POST',
        body: payload
      });

      form.reset();

      setModalMessage(
        'server-modal-message',
        'Servidor agregado. El monitor lo tomará en el siguiente ciclo.',
        'success'
      );

      toastr.success('Servidor agregado');
      await loadAdminServers();
    } catch (error) {
      setModalMessage('server-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo agregar');
    }
  }

  async function toggleServerEnabled(button) {
    if (!requireAdmin()) return;

    const serverId = button.dataset.serverId;
    const serverName = button.dataset.serverName || 'este servidor';
    const nextEnabled = button.dataset.enabled === '1';

    if (!serverId) return;

    const action = nextEnabled ? 'activar' : 'desactivar';
    const ok = confirm(`¿Seguro que quieres ${action} ${serverName}?`);

    if (!ok) return;

    setModalMessage('server-modal-message', `${nextEnabled ? 'Activando' : 'Desactivando'} ${serverName}...`);

    try {
      const response = await requestJSON(`/api/admin/servers/${encodeURIComponent(serverId)}`, {
        method: 'PATCH',
        body: {
          enabled: nextEnabled
        }
      });

      setModalMessage('server-modal-message', response.message || 'Servidor actualizado.', 'success');
      toastr.success('Servidor actualizado');

      await loadAdminServers();
    } catch (error) {
      setModalMessage('server-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo actualizar');
    }
  }

  async function deleteServer(button) {
    if (!requireAdmon()) return;

    const serverId = button.dataset.serverId;
    const serverName = button.dataset.serverName || 'este servidor';

    if (!serverId) return;

    const ok = confirm(`¿Seguro que quieres BORRAR DEFINITIVAMENTE ${serverName}? Esta acción no se puede deshacer.`);

    if (!ok) return;

    setModalMessage('server-modal-message', `Borrando ${serverName}...`);

    try {
      const response = await requestJSON(`/api/admin/servers/${encodeURIComponent(serverId)}`, {
        method: 'DELETE'
      });

      setModalMessage('server-modal-message', response.message || 'Servidor eliminado.', 'success');
      toastr.success('Servidor eliminado');

      await loadAdminServers();
    } catch (error) {
      setModalMessage('server-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo borrar');
    }
  }

  function renderUsersList(users = []) {
    const list = $('users-list');
    if (!list) return;

    if (!users.length) {
      list.innerHTML = `
        <div class="rounded-xl border border-dashed border-gray-300 p-6 text-center">
          <i class="fa-solid fa-user-slash text-gray-300 text-3xl mb-3"></i>
          <p class="text-sm text-gray-500">No hay usuarios creados todavía.</p>
        </div>
      `;
      return;
    }

    list.innerHTML = users.map(user => {
      const userId = escapeHTML(user.id);
      const username = escapeHTML(user.username || '');
      const email = escapeHTML(user.email || 'Sin correo');
      const role = String(user.rol || 'lector').toLowerCase();
      const safeRole = escapeHTML(role);
      const isActive = Number(user.is_active) === 1;
      const createdAt = escapeHTML(formatTimestamp(user.created_at) || user.created_at || 'N/A');
      const lastLogin = escapeHTML(formatTimestamp(user.last_login_at) || user.last_login_at || 'Nunca');
      const passwordChanged = escapeHTML(formatTimestamp(user.password_changed_at) || user.password_changed_at || 'N/A');

      return `
        <article class="admin-list-card user-admin-card">
          <div class="grid grid-cols-1 2xl:grid-cols-[minmax(0,1fr)_230px] gap-4 items-start">
            <div class="min-w-0">
              <div class="flex items-start gap-3">
                <div class="user-avatar flex-shrink-0">
                  <i class="fa-solid fa-user"></i>
                </div>

                <div class="min-w-0 flex-1">
                  <div class="flex flex-wrap items-center gap-2 mb-1">
                    <h5 class="font-bold text-gray-800 dark:text-gray-100 truncate max-w-full">
                      ${username}
                    </h5>

                    <span class="text-[10px] uppercase rounded-full px-2 py-1 ${roleBadgeClass(role)}">
                      ${safeRole}
                    </span>

                    <span class="text-[10px] uppercase rounded-full px-2 py-1 ${activeBadgeClass(user.is_active)}">
                      ${isActive ? 'Activo' : 'Inactivo'}
                    </span>
                  </div>

                  <p class="text-xs text-gray-500 dark:text-gray-400 truncate">
                    <i class="fa-solid fa-envelope mr-1 text-gray-400"></i>
                    ${email}
                  </p>
                </div>
              </div>

              <div class="mt-4 grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-2 text-xs">
                <div class="user-meta-box">
                  <span class="user-meta-label">
                    <i class="fa-regular fa-calendar-plus mr-1"></i>
                    Creado
                  </span>
                  <span class="user-meta-value">${createdAt}</span>
                </div>

                <div class="user-meta-box">
                  <span class="user-meta-label">
                    <i class="fa-solid fa-right-to-bracket mr-1"></i>
                    Último login
                  </span>
                  <span class="user-meta-value">${lastLogin}</span>
                </div>

                <div class="user-meta-box md:col-span-2 2xl:col-span-1">
                  <span class="user-meta-label">
                    <i class="fa-solid fa-key mr-1"></i>
                    Password actualizado
                  </span>
                  <span class="user-meta-value">${passwordChanged}</span>
                </div>
              </div>
            </div>

            ${isAdmon() ? `
              <div class="user-action-panel">
                <label class="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                  Rol
                </label>

                <select
                  class="user-role-select w-full text-xs rounded-lg border border-gray-300 dark:border-gray-700 bg-white dark:bg-[#0D1117] text-gray-700 dark:text-gray-200 px-3 py-2"
                  data-user-id="${userId}"
                  title="Cambiar rol">
                  <option value="lector" ${role === 'lector' ? 'selected' : ''}>Lector</option>
                  <option value="admin" ${role === 'admin' ? 'selected' : ''}>Admin</option>
                  <option value="admon" ${role === 'admon' ? 'selected' : ''}>Admon</option>
                </select>

                <button
                  type="button"
                  class="toggle-user-active-btn user-action-btn ${isActive ? 'user-action-warning' : 'user-action-success'}"
                  data-user-id="${userId}"
                  data-active="${isActive ? '0' : '1'}"
                  data-username="${username}">
                  <i class="fa-solid ${isActive ? 'fa-user-slash' : 'fa-user-check'}"></i>
                  <span>${isActive ? 'Desactivar' : 'Activar'}</span>
                </button>

                <button
                  type="button"
                  class="reset-password-btn user-action-btn user-action-info"
                  data-user-id="${userId}"
                  data-username="${username}">
                  <i class="fa-solid fa-key"></i>
                  <span>Reset pass</span>
                </button>

                <button
                  type="button"
                  class="delete-user-btn user-action-btn user-action-danger"
                  data-user-id="${userId}"
                  data-username="${username}">
                  <i class="fa-solid fa-trash-can"></i>
                  <span>Eliminar</span>
                </button>
              </div>
            ` : `
              <div class="user-action-panel user-action-panel-readonly">
                <i class="fa-solid fa-eye text-gray-400 text-xl"></i>
                <p class="text-xs text-gray-500 dark:text-gray-400 text-center">
                  Solo lectura
                </p>
              </div>
            `}
          </div>
        </article>
      `;
    }).join('');
  }

  async function loadAdminUsers() {
    setModalMessage('user-modal-message', 'Cargando usuarios...');

    try {
      const payload = await requestJSON('/api/admin/users');

      renderUsersList(payload.users || []);
      setModalMessage('user-modal-message', '');
    } catch (error) {
      renderUsersList([]);
      setModalMessage('user-modal-message', error.message, 'error');
    }
  }

  function openUserModal() {
    if (!requireAdmin()) return;

    toggleSettingsMenu(false);
    openModal('user-modal');
    setModalMessage('user-modal-message', '');
    loadAdminUsers();
  }

  async function submitUserForm(event) {
    event.preventDefault();

    if (!requireAdmin()) return;

    const form = event.currentTarget;
    const data = new FormData(form);

    const payload = {
      email: String(data.get('email') || '').trim(),
      username: String(data.get('username') || '').trim(),
      password: String(data.get('password') || ''),
      rol: String(data.get('rol') || 'lector')
    };

    setModalMessage('user-modal-message', 'Creando usuario...');

    try {
      const response = await requestJSON('/api/admin/users', {
        method: 'POST',
        body: payload
      });

      form.reset();

      setModalMessage(
        'user-modal-message',
        response.message || 'Usuario creado. Código por correo sigue en standby.',
        'success'
      );

      toastr.success('Usuario creado');
      await loadAdminUsers();
    } catch (error) {
      setModalMessage('user-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo crear');
    }
  }

  async function updateUserRole(select) {
    if (!requireAdmon()) return;

    const userId = select.dataset.userId;
    const role = select.value;

    if (!userId) return;

    setModalMessage('user-modal-message', 'Actualizando rol...');

    try {
      const response = await requestJSON(`/api/admin/users/${encodeURIComponent(userId)}`, {
        method: 'PATCH',
        body: { rol: role }
      });

      setModalMessage('user-modal-message', response.message || 'Rol actualizado.', 'success');
      toastr.success('Rol actualizado');
      await loadAdminUsers();
    } catch (error) {
      setModalMessage('user-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo actualizar');
      await loadAdminUsers();
    }
  }

  async function toggleUserActive(button) {
    if (!requireAdmon()) return;

    const userId = button.dataset.userId;
    const username = button.dataset.username || 'este usuario';
    const nextActive = button.dataset.active === '1';

    if (!userId) return;

    const action = nextActive ? 'activar' : 'desactivar';
    const ok = confirm(`¿Seguro que quieres ${action} a ${username}?`);

    if (!ok) return;

    setModalMessage('user-modal-message', `${nextActive ? 'Activando' : 'Desactivando'} a ${username}...`);

    try {
      const response = await requestJSON(`/api/admin/users/${encodeURIComponent(userId)}`, {
        method: 'PATCH',
        body: { is_active: nextActive }
      });

      setModalMessage('user-modal-message', response.message || 'Usuario actualizado.', 'success');
      toastr.success('Usuario actualizado');
      await loadAdminUsers();
    } catch (error) {
      setModalMessage('user-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo actualizar');
    }
  }

  async function resetUserPassword(button) {
    if (!requireAdmon()) return;

    const userId = button.dataset.userId;
    const username = button.dataset.username || 'este usuario';

    if (!userId) return;

    const password = prompt(`Nuevo password temporal para ${username} (mínimo 8 caracteres):`);

    if (password === null) return;

    if (password.length < 8) {
      toastr.warning('Password mínimo de 8 caracteres');
      return;
    }

    setModalMessage('user-modal-message', `Actualizando password de ${username}...`);

    try {
      const response = await requestJSON(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
        method: 'POST',
        body: { password }
      });

      setModalMessage('user-modal-message', response.message || 'Password actualizado.', 'success');
      toastr.success('Password actualizado');
      await loadAdminUsers();
    } catch (error) {
      setModalMessage('user-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo actualizar password');
    }
  }

  async function deleteUser(button) {
    if (!requireAdmon()) return;

    const userId = button.dataset.userId;
    const username = button.dataset.username || 'este usuario';

    if (!userId) return;

    const ok = confirm(`¿Seguro que quieres eliminar a ${username}?`);

    if (!ok) return;

    setModalMessage('user-modal-message', `Eliminando a ${username}...`);

    try {
      await requestJSON(`/api/admin/users/${encodeURIComponent(userId)}`, {
        method: 'DELETE'
      });

      setModalMessage('user-modal-message', 'Usuario eliminado.', 'success');
      toastr.success('Usuario eliminado');

      await loadAdminUsers();
    } catch (error) {
      setModalMessage('user-modal-message', error.message, 'error');
      toastr.error(error.message, 'No se pudo eliminar');
    }
  }

  function auditActionLabel(action) {
    const labels = {
      'auth.login_success': 'Inicio de sesión',
      'auth.login_failed': 'Login fallido',
      'auth.logout': 'Cierre de sesión',
      'sensor.pause': 'Sensor pausado',
      'sensor.resume': 'Sensor reanudado',
      'sensor.pin': 'Sensor fijado',
      'sensor.unpin': 'Sensor desfijado',
      'server.create': 'Servidor creado',
      'server.update': 'Servidor actualizado',
      'server.delete': 'Servidor eliminado',
      'user.create': 'Usuario creado',
      'user.update': 'Usuario actualizado',
      'user.reset_password': 'Password reiniciado',
      'user.delete': 'Usuario eliminado',
      'alert.settings_update': 'Alertas actualizadas',
      'alert.sound_upload': 'Sonido cargado',
      'alert.sound_reset': 'Sonido restaurado',
      'logs.export': 'Logs exportados'
    };

    return labels[action] || action || 'Acción';
  }

  function auditActionClass(action) {
    if (String(action).includes('failed') || String(action).includes('delete')) {
      return 'audit-action-danger';
    }

    if (String(action).includes('login_success') || String(action).includes('resume')) {
      return 'audit-action-success';
    }

    if (String(action).includes('pause') || String(action).includes('update')) {
      return 'audit-action-warning';
    }

    return 'audit-action-info';
  }

  function prettyAuditDetail(raw) {
    if (!raw) return '';

    try {
      return JSON.stringify(JSON.parse(raw), null, 2);
    } catch {
      return String(raw);
    }
  }

  function populateAuditSelect(id, values = [], placeholder = 'Todos') {
    const select = $(id);
    if (!select) return;

    const current = select.value;
    select.innerHTML = `<option value="">${escapeHTML(placeholder)}</option>`;

    values.forEach(value => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });

    if ([...select.options].some(option => option.value === current)) {
      select.value = current;
    }
  }

  function renderAuditLogs(logs = []) {
    const body = $('audit-table-body');
    const count = $('audit-result-count');

    if (count) {
      count.textContent = `${logs.length} evento${logs.length === 1 ? '' : 's'}`;
    }

    if (!body) return;

    if (!logs.length) {
      body.innerHTML = `
        <tr>
          <td colspan="7" class="audit-empty">
            <i class="fa-solid fa-magnifying-glass mr-2"></i>
            No hay eventos con esos filtros.
          </td>
        </tr>
      `;
      return;
    }

    body.innerHTML = logs.map(log => {
      const action = String(log.action || '');
      const target = log.target_name || log.target_id || '-';
      const detail = prettyAuditDetail(log.detail);
      const requestLabel = [log.request_method, log.request_path]
        .filter(Boolean)
        .join(' ');

      return `
        <tr>
          <td class="audit-date-cell">
            <span>${escapeHTML(formatTimestamp(log.created_at) || log.created_at || '-')}</span>
          </td>

          <td>
            <div class="audit-user-cell">
              <span class="audit-user-avatar">
                <i class="fa-solid fa-user"></i>
              </span>
              <div>
                <strong>${escapeHTML(log.username || 'system')}</strong>
                <small>ID ${escapeHTML(log.user_id ?? '-')}</small>
              </div>
            </div>
          </td>

          <td>
            <span class="audit-action-badge ${auditActionClass(action)}">
              ${escapeHTML(auditActionLabel(action))}
            </span>
            <small class="audit-action-code">${escapeHTML(action)}</small>
          </td>

          <td>
            <strong class="audit-target-name">${escapeHTML(target)}</strong>
            <small>${escapeHTML(log.target_type || '-')} · ${escapeHTML(log.target_id || '-')}</small>
          </td>

          <td>
            <span class="audit-ip-badge">${escapeHTML(log.ip_address || '-')}</span>
            <small class="audit-user-agent" title="${escapeHTML(log.user_agent || '')}">
              ${escapeHTML((log.user_agent || 'Sin User-Agent').slice(0, 44))}
            </small>
          </td>

          <td>
            <code class="audit-request-code">${escapeHTML(requestLabel || '-')}</code>
          </td>

          <td>
            ${detail ? `
              <details class="audit-detail">
                <summary>Ver detalle</summary>
                <pre>${escapeHTML(detail)}</pre>
              </details>
            ` : '<span class="text-gray-600">Sin detalle</span>'}
          </td>
        </tr>
      `;
    }).join('');
  }

  async function loadAuditLogs() {
    if (!isAdmin()) return;

    const form = $('audit-filter-form');
    const params = new URLSearchParams();

    if (form) {
      const data = new FormData(form);

      for (const [key, value] of data.entries()) {
        const clean = String(value || '').trim();

        if (clean) {
          params.set(key, clean);
        }
      }
    }

    setModalMessage('audit-modal-message', 'Cargando eventos de auditoría...');

    try {
      const payload = await requestJSON(`/api/admin/audit?${params.toString()}`);

      populateAuditSelect(
        'audit-user-filter',
        payload.filters?.usernames || [],
        'Todos'
      );
      populateAuditSelect(
        'audit-action-filter',
        payload.filters?.actions || [],
        'Todas'
      );
      populateAuditSelect(
        'audit-target-filter',
        payload.filters?.target_types || [],
        'Todos'
      );

      renderAuditLogs(payload.logs || []);
      setModalMessage('audit-modal-message', '');
    } catch (error) {
      renderAuditLogs([]);
      setModalMessage('audit-modal-message', error.message, 'error');
    }
  }

  function openAuditModal() {
    if (!requireAdmin()) return;

    toggleSettingsMenu(false);
    openModal('audit-modal');
    loadAuditLogs();
  }

  function resetAuditFilters() {
    $('audit-filter-form')?.reset();
    loadAuditLogs();
  }

  function getStatusSummary(sensors) {
    const summary = {
      total: sensors.length,
      up: 0,
      warning: 0,
      down: 0,
      down_ack: 0,
      paused: 0,
      unusual: 0,
      unknown: 0
    };

    for (const sensor of sensors) {
      const status = String(sensor.status || 'Unknown');

      if (status === 'Up') {
        summary.up += 1;
      } else if (status === 'Warning') {
        summary.warning += 1;
      } else if (['Down', 'Down Partial'].includes(status)) {
        summary.down += 1;
      } else if (status === 'Down Acknowledged') {
        summary.down_ack += 1;
      } else if (isSensorPaused(sensor)) {
        summary.paused += 1;
      } else if (status === 'Unusual') {
        summary.unusual += 1;
      } else {
        summary.unknown += 1;
      }
    }

    return summary;
  }

  function bump(id) {
    const el = $(id);
    if (!el) return;

    el.classList.add('scale-105');

    setTimeout(() => {
      el.classList.remove('scale-105');
    }, 300);
  }

  function assignCounter(id, value, cardId) {
    const el = $(id);
    if (!el) return;

    const previous = el.textContent;
    el.textContent = String(value);

    if (previous !== String(value)) {
      bump(cardId);
    }
  }

  function renderCounters() {
    const sensors = sensorData?.sensores || [];
    const counts = getStatusSummary(sensors);

    assignCounter('total-sensors', counts.total, 'card-total');
    assignCounter('up-sensors', counts.up, 'card-up');
    assignCounter('down-sensors', counts.down, 'card-down');
    assignCounter('warning-sensors', counts.warning, 'card-warning');
    assignCounter('paused-sensors', counts.paused, 'card-paused');
    assignCounter('acknowledged-sensors', counts.down_ack, 'card-ack');

    if ($('last-updated')) {
      $('last-updated').textContent = `Actualizado: ${formatTimestamp(sensorData?.ultima_actualizacion) || 'N/A'}`;
    }
  }

  function updateStatusChart() {
  const canvas = $('statusChart');
  if (!canvas) return;

  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const summary = getStatusSummary(sensorData?.sensores || []);

  /* Paleta alineada con tus sensores */
  const palette = {
    down: '#dc2626',       // rojo
    down_ack: '#9333ea',   // morado
    warning: '#eab308',    // amarillo
    up: '#16a34a',         // verde
    paused: '#0ea5e9',     // sky
    unusual: '#64748b',    // slate
    unknown: '#6b7280'     // gray
  };

  const items = [
    {
      key: 'down',
      label: 'Fallo',
      short: '!!',
      value: summary.down,
      color: palette.down
    },
    {
      key: 'down_ack',
      label: 'Fallo (confirmado)',
      short: '!',
      value: summary.down_ack,
      color: palette.down_ack
    },
    {
      key: 'warning',
      label: 'Advertencia',
      short: 'W',
      value: summary.warning,
      color: palette.warning
    },
    {
      key: 'up',
      label: 'OK',
      short: '✓',
      value: summary.up,
      color: palette.up
    },
    {
      key: 'paused',
      label: 'Pausado',
      short: '‖',
      value: summary.paused,
      color: palette.paused
    },
    {
      key: 'unusual',
      label: 'Inusual',
      short: 'U',
      value: summary.unusual,
      color: palette.unusual
    },
    {
      key: 'unknown',
      label: 'Desconocido',
      short: '?',
      value: summary.unknown,
      color: palette.unknown
    }
  ];

  const data = {
    labels: items.map(item => item.label),
    datasets: [{
      data: items.map(item => item.value),
      backgroundColor: items.map(item => item.color),
      borderColor: '#090909',
      borderWidth: 2,
      hoverOffset: 4,
      cutout: '66%',
      radius: '96%'
    }]
  };

  const options = {
    responsive: true,
    maintainAspectRatio: true,
    aspectRatio: 1,
    animation: {
      duration: 350
    },
    layout: {
      padding: 6
    },
    plugins: {
      legend: {
        display: false
      },
      tooltip: {
        backgroundColor: '#090909',
        borderColor: '#27272a',
        borderWidth: 1,
        titleColor: '#f4f4f5',
        bodyColor: '#e4e4e7',
        callbacks: {
          label(context) {
            const value = context.parsed || 0;
            const pct = summary.total ? ((value / summary.total) * 100).toFixed(1) : '0.0';
            return `${context.label}: ${value} (${pct}%)`;
          }
        }
      },
      centerText: {
        total: summary.total
      }
    }
  };

  if (!statusChart) {
    statusChart = new Chart(ctx, {
      type: 'doughnut',
      data,
      options
    });
  } else {
    statusChart.data = data;
    statusChart.options = options;
    statusChart.resize();
    statusChart.update('none');
  }
}

  function populateServerFilter() {
    const select = $('server-filter');
    if (!select) return;

    const currentValue = select.value;

    const servers = [
      ...new Set((sensorData?.sensores || []).map(item => item.grupo).filter(Boolean))
    ].sort();

    select.innerHTML = '<option value="all">Todos los Servidores</option>';

    for (const server of servers) {
      const option = document.createElement('option');
      option.value = server;
      option.textContent = server;
      select.appendChild(option);
    }

    if ([...select.options].some(option => option.value === currentValue)) {
      select.value = currentValue;
    }
  }

  function getFilteredSensors() {
    const sensors = sensorData?.sensores || [];
    const searchTerm = String($('search')?.value || '').toLowerCase();
    const statusFilter = $('status-filter')?.value || 'all';
    const serverFilter = $('server-filter')?.value || 'all';

    return sensors.filter(sensor => {
      const status = sensor.status || 'Unknown';

      const matchesSearch = [
        sensor.sensor,
        sensor.device,
        sensor.ip_address,
        sensor.grupo
      ].some(value => String(value || '').toLowerCase().includes(searchTerm));

      const matchesServer = serverFilter === 'all' || sensor.grupo === serverFilter;

      let matchesStatus = false;

      if (statusFilter === 'all') {
        matchesStatus = true;
      } else if (statusFilter === 'Down') {
        matchesStatus = ['Down', 'Down Partial'].includes(status);
      } else if (statusFilter === 'Paused') {
        matchesStatus = isSensorPaused(sensor);
      } else if (statusFilter === 'Unknown') {
        matchesStatus = ['Unknown', 'Unusual', 'Scanning', 'None'].includes(status);
      } else {
        matchesStatus = status === statusFilter;
      }

      return matchesSearch && matchesServer && matchesStatus;
    });
  }

  function statusOrderKey(sensor) {
    const status = String(sensor.status || '').toLowerCase();

    if (status === 'down' || status === 'down partial') return 0;
    if (status === 'warning') return 1;
    if (status === 'up') return 2;
    if (status.includes('acknowledged')) return 3;
    if (isSensorPaused(sensor)) return 4;
    if (status === 'unusual') return 5;
    if (['unknown', 'scanning', 'none'].includes(status)) return 6;

    return 7;
  }

  function sortSensors(list) {
    return [...list].sort((a, b) => {
      const priority = statusOrderKey(a) - statusOrderKey(b);

      if (priority) return priority;

      return String(a.device || '').localeCompare(String(b.device || '')) ||
        String(a.sensor || '').localeCompare(String(b.sensor || ''));
    });
  }


  function sensorPinKey(group, id) {
  return JSON.stringify([
    String(group || ''),
    String(id || '')
  ]);
}

function isSensorPinned(sensor) {
  return pinnedSensorKeys.has(
    sensorPinKey(
      sensor.grupo || sensor.group,
      sensor.sensor_id || sensor.id
    )
  );
}

async function loadPinnedSensors() {
  try {
    const payload = await requestJSON('/api/me/pins');

    pinnedSensorKeys = new Set(
      (payload.pins || []).map(pin =>
        sensorPinKey(pin.group, pin.id)
      )
    );
  } catch (error) {
    console.warn('No se pudieron cargar los sensores fijados:', error);
    pinnedSensorKeys = new Set();
  }
}

async function toggleSensorPin(button) {
  const group = String(button.dataset.group || '').trim();
  const id = String(button.dataset.id || '').trim();
  const sensorName = String(button.dataset.sensor || '').trim();
  const deviceName = String(button.dataset.device || '').trim();

  if (!group || !id) {
    toastr.warning('El sensor no tiene identificador válido');
    return;
  }

  const currentlyPinned = button.dataset.pinned === 'true';
  const nextPinned = !currentlyPinned;

  button.disabled = true;
  button.classList.add('is-loading');

  try {
    const response = await requestJSON('/api/me/pins', {
      method: nextPinned ? 'POST' : 'DELETE',
      body: {
        group,
        id,
        sensor_name: sensorName,
        device_name: deviceName
      }
    });

    const key = sensorPinKey(group, id);

    if (nextPinned) {
      pinnedSensorKeys.add(key);
    } else {
      pinnedSensorKeys.delete(key);
    }

    renderSensorsView();

    toastr.success(
      response.message ||
      (nextPinned
        ? 'Sensor agregado a fijados'
        : 'Sensor retirado de fijados')
    );
  } catch (error) {
    button.disabled = false;
    button.classList.remove('is-loading');

    toastr.error(
      error.message,
      'No se pudo actualizar fijados'
    );
  }
}

  function formatSlaDuration(totalSeconds) {
    const safe = Math.max(0, Math.floor(Math.abs(Number(totalSeconds) || 0)));
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const seconds = safe % 60;

    return [
      String(hours).padStart(2, '0'),
      String(minutes).padStart(2, '0'),
      String(seconds).padStart(2, '0')
    ].join(':');
  }

  function updateMFTimers() {
    const now = Date.now();

    document.querySelectorAll('.mf-sla-widget[data-mf-deadline]').forEach(widget => {
      const deadlineRaw = widget.dataset.mfDeadline;
      const deadline = new Date(deadlineRaw).getTime();

      if (!Number.isFinite(deadline)) return;

      const remainingSeconds = Math.floor((deadline - now) / 1000);
      const breached = remainingSeconds < 0;

      widget.classList.toggle('is-breached', breached);
      widget.classList.toggle('is-running', !breached);

      const time = widget.querySelector('.mf-sla-time');
      const state = widget.querySelector('.mf-sla-state');

      if (time) {
        time.textContent = formatSlaDuration(remainingSeconds);
      }

      if (state) {
        state.textContent = breached ? 'Vencido' : 'En curso';
      }
    });
  }

  function ensureMFTimerTicker() {
    updateMFTimers();

    if (mfTimerInterval) return;

    mfTimerInterval = setInterval(updateMFTimers, 1000);
  }

  function mfSlaHTML(sensor) {
    if (!sensor.is_mf) return '';

    const active = Boolean(sensor.mf_alert_active && sensor.mf_sla_deadline);
    const breached = Boolean(sensor.mf_sla_breached);

    if (!active) {
      return `
        <div class="mf-sla-widget is-idle">
          <div class="mf-sla-heading">
            <span class="mf-sla-label">MF:</span>
            <span class="mf-sla-state">Sin alerta</span>
          </div>
        </div>
      `;
    }

    return `
      <div
        class="mf-sla-widget ${breached ? 'is-breached' : 'is-running'}"
        data-mf-deadline="${escapeHTML(sensor.mf_sla_deadline)}">
        <div class="mf-sla-heading">
          <span class="mf-sla-label">MF:</span>
          <span class="mf-sla-state">${breached ? 'Vencido' : 'En curso'}</span>
        </div>
        <strong class="mf-sla-time">--:--:--</strong>
      </div>
    `;
  }

  function sensorCardHTML(sensor) {
    const rawStatus = sensor.status || 'Unknown';
    const paused = isSensorPaused(sensor);
    const status = paused && rawStatus === 'Unknown' ? 'Paused' : rawStatus;
    const info = statusConfig[status] || statusConfig.default;
    const ip = sensor.ip_address || 'N/A';
    const sensorId = sensor.sensor_id || '';
    const canHistory = sensorId && sensorId !== 'N/A' && sensor.grupo;
    const pinned = isSensorPinned(sensor);

    const lastValue = status === 'Paused Until'
      ? `
        <div class="text-xs text-gray-600 flex items-center">
          <i class="fas fa-calendar-check w-4 text-center mr-1.5 text-prtg-paused"></i>
          <span>
            Reanuda:
            <strong class="font-medium">
              ${escapeHTML(String(sensor.message || '').replace('Paused until ', '').trim() || 'N/A')}
            </strong>
          </span>
        </div>
      `
      : `
        <p class="text-xs text-gray-500">Último Valor</p>
        <p class="font-semibold text-sm ${info.textColor}">
          ${escapeHTML(sensor.lastvalue || 'N/A')}
        </p>
      `;

    return `

      <button
  type="button"
  class="sensor-pin-btn ${pinned ? 'is-pinned' : ''}"
  title="${pinned ? 'Quitar de fijados' : 'Agregar a fijados'}"
  aria-label="${pinned ? 'Quitar sensor de fijados' : 'Fijar sensor'}"
  aria-pressed="${pinned ? 'true' : 'false'}"
  data-pinned="${pinned ? 'true' : 'false'}"
  data-group="${escapeHTML(sensor.grupo)}"
  data-id="${escapeHTML(sensorId)}"
  data-device="${escapeHTML(sensor.device)}"
  data-sensor="${escapeHTML(sensor.sensor)}">
  <i class="fa-solid fa-thumbtack"></i>
</button>

      <div>
        <div class="flex justify-between items-start mb-3 gap-2">
          <div class="flex-1 min-w-0">
            <h3 class="font-medium text-gray-800 break-words" title="${escapeHTML(sensor.device)}">
              ${escapeHTML(sensor.device || 'Unknown')}
            </h3>

            <p class="text-sm text-gray-500 break-words" title="${escapeHTML(sensor.sensor)}">
              ${escapeHTML(sensor.sensor || 'Unknown')}
            </p>

            ${ip !== 'N/A' ? `
              <div class="mt-2">
                <span class="ip-badge text-xs font-semibold px-2 py-1 rounded-full inline-flex items-center">
                  <i class="fas fa-globe mr-1.5"></i>${escapeHTML(ip)}
                </span>
              </div>
            ` : ''}

            <p class="text-xs text-gray-400 italic mt-1">${escapeHTML(sensor.grupo || '')}</p>

            ${mfSlaHTML(sensor)}
          </div>

          <span class="status-badge ${info.bgColor} ${info.textColor} text-xs font-semibold px-2 py-1 rounded-full flex items-center justify-center ml-2 flex-shrink-0">
            <i class="fas ${info.icon} mr-1.5"></i>
            <span>${escapeHTML(paused ? 'Paused' : status)}</span>
          </span>
        </div>
      </div>

      <div class="border-t pt-2 mt-auto flex items-end justify-between gap-3">
        <div>${lastValue}</div>

        ${canHistory ? `
  <div class="sensor-card-actions">
    <button
      type="button"
      class="sensor-pause-btn sensor-action-icon ${paused ? 'resume' : 'pause'}"
      title="${paused ? 'Reanudar escaneo' : 'Pausar sensor'}"
      data-action="${paused ? 'resume' : 'pause'}"
      data-group="${escapeHTML(sensor.grupo)}"
      data-id="${escapeHTML(sensorId)}"
      data-device="${escapeHTML(sensor.device)}"
      data-sensor="${escapeHTML(sensor.sensor)}"
      data-status="${escapeHTML(status)}"
      data-title="${escapeHTML(sensor.device)} - ${escapeHTML(sensor.sensor)}">
      <i class="fa-solid ${paused ? 'fa-play' : 'fa-pause'}"></i>
    </button>

    <button
      type="button"
      class="history-btn sensor-action-icon history"
      title="Ver histórico"
      data-group="${escapeHTML(sensor.grupo)}"
      data-id="${escapeHTML(sensorId)}"
      data-device="${escapeHTML(sensor.device)}"
      data-sensor="${escapeHTML(sensor.sensor)}"
      data-status="${escapeHTML(status)}"
      data-title="${escapeHTML(sensor.device)} - ${escapeHTML(sensor.sensor)}">
      <i class="fa-solid fa-chart-line"></i>
    </button>
  </div>
` : ''}
      </div>
    `;
  }

  function createSensorCard(sensor) {
  const info =
    statusConfig[sensor.status || 'Unknown'] ||
    statusConfig.default;

  const card = document.createElement('article');

  card.className = `
    sensor-card
    relative
    bg-white
    rounded-lg
    shadow-sm
    p-4
    transition-all
    duration-200
    border-l-4
    ${info.borderColor}
    min-h-[180px]
    flex
    flex-col
    justify-between
  `;

  const isMFAlerted = Boolean(
    sensor?.is_mf &&
    sensor?.mf_alert_active &&
    sensor?.mf_sla_deadline
  );

  if (isMFAlerted) {
    card.classList.add('mf-alert-card');
  }

  card.innerHTML = sensorCardHTML(sensor);

  return card;
}

function renderSensorsView() {
  const grid = $('sensor-grid');
  const pinnedGrid = $('pinned-sensor-grid');
  const pinnedSection = $('pinned-sensors-section');
  const pinnedCount = $('pinned-sensors-count');
  const noResults = $('no-results');

  if (!grid || !pinnedGrid || !pinnedSection) return;

  const filtered = sortSensors(getFilteredSensors());

  const pinnedSensors = filtered.filter(sensor =>
    isSensorPinned(sensor)
  );

  const regularSensors = filtered.filter(sensor =>
    !isSensorPinned(sensor)
  );

  grid.innerHTML = '';
  pinnedGrid.innerHTML = '';

  for (const sensor of pinnedSensors) {
    pinnedGrid.appendChild(createSensorCard(sensor));
  }

  for (const sensor of regularSensors) {
    grid.appendChild(createSensorCard(sensor));
  }

  pinnedSection.classList.toggle(
    'hidden',
    pinnedSensors.length === 0
  );

  if (pinnedCount) {
    pinnedCount.textContent = String(pinnedSensors.length);
  }

  const hasResults =
    pinnedSensors.length > 0 ||
    regularSensors.length > 0;

  noResults?.classList.toggle('hidden', hasResults);
  grid.classList.toggle('hidden', regularSensors.length === 0);

  ensureMFTimerTicker();
}

  function filenameFromDisposition(header, fallback) {
    const value = String(header || '');
    const utfMatch = value.match(/filename\*=UTF-8''([^;]+)/i);

    if (utfMatch?.[1]) {
      try {
        return decodeURIComponent(utfMatch[1]);
      } catch {
        return utfMatch[1];
      }
    }

    const plainMatch = value.match(/filename="?([^";]+)"?/i);
    return plainMatch?.[1] || fallback;
  }

  async function exportSensorLogs(hours, button = null) {
    const allowed = new Set([12, 24, 72]);
    const windowHours = Number(hours);

    if (!allowed.has(windowHours)) {
      toastr.warning('Ventana de exportación inválida');
      return;
    }

    const originalHTML = button?.innerHTML;

    if (button) {
      button.disabled = true;
      button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Generando...';
    }

    try {
      const response = await fetch(`/api/logs/export?hours=${windowHours}`, {
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store'
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      const blob = await response.blob();
      const fallback = `logs_sensores_${windowHours}h.csv`;
      const filename = filenameFromDisposition(
        response.headers.get('Content-Disposition'),
        fallback
      );

      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');

      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();

      setTimeout(() => URL.revokeObjectURL(url), 1000);
      toastr.success(`Reporte de ${windowHours} horas generado`);
    } catch (error) {
      toastr.error(error.message, 'No se pudo exportar');
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = originalHTML;
      }
    }
  }

  function renderLogView() {
    const upContainer = $('up-logs-container');
    const downContainer = $('down-logs-container');

    if (!upContainer || !downContainer) return;

    upContainer.innerHTML = '';
    downContainer.innerHTML = '';

    function createLogEntry(log, type) {
      const info = type === 'Up' ? statusConfig.Up : statusConfig.Down;
      const entry = document.createElement('div');

      entry.className = 'log-entry border rounded-md p-3 mb-2 flex flex-col sm:flex-row sm:items-center sm:justify-between';

      entry.innerHTML = `
        <div class="flex-1 min-w-0 mb-2 sm:mb-0">
          <div class="font-medium truncate" title="${escapeHTML(log.sensor)}">
            ${escapeHTML(log.sensor)}
          </div>

          <div class="text-sm text-gray-500 truncate">
            ${escapeHTML(log.device)}
            <span class="text-xs">(${escapeHTML(log.grupo)})</span>
          </div>
        </div>

        <div class="text-xs text-gray-600 flex flex-col sm:items-end">
          <span class="font-mono mb-1">
            ${escapeHTML(formatTimestamp(log.timestamp) || log.timestamp)}
          </span>

          <span class="bg-gray-100 px-2 py-0.5 rounded">
            ${escapeHTML(log.previous_status || '?')}
            →
            <strong class="${info.textColor}">
              ${escapeHTML(log.new_status || '')}
            </strong>
          </span>
        </div>
      `;

      return entry;
    }

    const logs = sensorData?.logs || {};

    if (logs.last_up?.length) {
      logs.last_up.forEach(log => upContainer.appendChild(createLogEntry(log, 'Up')));
    } else {
      upContainer.innerHTML = '<p class="text-center text-gray-400 py-4">No hay eventos de recuperación recientes.</p>';
    }

    if (logs.last_down?.length) {
      logs.last_down.forEach(log => downContainer.appendChild(createLogEntry(log, 'Down')));
    } else {
      downContainer.innerHTML = '<p class="text-center text-gray-400 py-4">No hay eventos de fallo recientes.</p>';
    }
  }

  function renderMetricsChart() {
    const ctx = $('metricsChart')?.getContext('2d');
    if (!ctx) return;

    const series = sensorData?.metrics?.series || [];

    if ($('metrics-updated')) {
      $('metrics-updated').textContent = `${series.length} puntos`;
    }

    const labels = series.map(item => item.t);

    const data = {
      labels,
      datasets: [
        {
          label: 'Up',
          data: series.map(item => item.up),
          borderColor: '#16a34a',
          backgroundColor: 'rgba(22,163,74,.08)',
          tension: 0.25
        },
        {
          label: 'Warning',
          data: series.map(item => item.warning),
          borderColor: '#eab308',
          backgroundColor: 'rgba(234,179,8,.08)',
          tension: 0.25
        },
        {
          label: 'Down',
          data: series.map(item => item.down),
          borderColor: '#dc2626',
          backgroundColor: 'rgba(220,38,38,.08)',
          tension: 0.25
        }
      ]
    };

    const options = {
      responsive: true,
      interaction: {
        mode: 'index',
        intersect: false
      },
      plugins: {
        legend: {
          position: 'bottom'
        }
      },
      scales: {
        x: {
          type: 'time',
          time: {
            tooltipFormat: 'yyyy-MM-dd HH:mm'
          }
        },
        y: {
          beginAtZero: true
        }
      }
    };

    if (!metricsChart) {
      metricsChart = new Chart(ctx, {
        type: 'line',
        data,
        options
      });
    } else {
      metricsChart.data = data;
      metricsChart.update();
    }
  }

  function setView(view) {
    currentView = view;

    $('sensor-grid-view')?.classList.toggle('hidden', view !== 'sensors');
    $('logs-view')?.classList.toggle('hidden', view !== 'logs');
    $('trends-view')?.classList.toggle('hidden', view !== 'trends');

    for (const button of document.querySelectorAll('.tab-button')) {
      const active = button.dataset.view === view;

      button.classList.toggle('border-b-2', active);
      button.classList.toggle('border-blue-500', active);
      button.classList.toggle('text-blue-600', active);
      button.classList.toggle('text-gray-500', !active);
    }

    if (view === 'trends') {
      renderMetricsChart();
    }
  }

  function renderAll() {
    if (!sensorData || !Array.isArray(sensorData.sensores)) {
      showError('El archivo de datos no contiene sensores.');
      return;
    }

    hideLoading();
    populateServerFilter();
    renderCounters();
    renderSensorsView();
    renderLogView();
    updateStatusChart();

    if (currentView === 'trends') {
      renderMetricsChart();
    }
  }

  async function loadData() {
    try {
      showLoading();

      const nextData = await fetchJSON(`${API_URL}?t=${Date.now()}`);

      handleSoundAlerts(null, nextData);

      sensorData = nextData;

      renderAll();
      startAutoRefresh();
    } catch (error) {
      console.error('Error al cargar datos:', error);
      showError(`No se pudieron cargar los datos. ${error.message}`);
    }
  }

  function startAutoRefresh() {
    if (refreshInterval) {
      clearInterval(refreshInterval);
    }

    refreshInterval = setInterval(checkForUpdates, REFRESH_MS);
  }

  async function checkForUpdates() {
    try {
      const next = await fetchJSON(`${API_URL}?t=${Date.now()}`, 1);

      if (!sensorData || next.ultima_actualizacion !== sensorData.ultima_actualizacion) {
        const previousData = sensorData;

        handleSoundAlerts(previousData, next);

        sensorData = next;
        renderAll();
      } else if ($('last-updated')) {
        $('last-updated').textContent = `Actualizado: ${formatTimestamp(next.ultima_actualizacion) || 'N/A'}`;
      }
    } catch (error) {
      console.warn('checkForUpdates error:', error);
    }
  }

  function openHistoryModal() {
    $('history-modal')?.classList.remove('hidden');
    $('history-modal')?.classList.add('flex');
  }

  function setHistoryMode(message = '', legacy = false) {
    const badge = $('history-mode');
    if (!badge) return;
    badge.textContent = message;
    badge.classList.toggle('hidden', !message);
    badge.classList.toggle('is-legacy', Boolean(legacy));
  }

  function setHistoryState(message = '', kind = '') {
    const state = $('history-state');
    if (!state) return;
    state.textContent = message;
    state.className = 'history-state';
    if (kind) state.classList.add(`is-${kind}`);
    state.classList.toggle('hidden', !message);
  }

  function setHistoryDiagnostics(message = '') {
    const diagnostics = $('history-diagnostics');
    if (!diagnostics) return;
    diagnostics.textContent = message;
    diagnostics.classList.toggle('hidden', !message);
  }

  function setHistoryError(message = '') {
    const errorBox = $('history-error');
    if (!errorBox) return;
    errorBox.textContent = message;
    errorBox.classList.toggle('hidden', !message);
  }

  function destroyHistoryCharts() {
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
    }
    METRICS_HISTORY?.destroyChartInstances(normalizedHistoryCharts);
    document.querySelectorAll('.metrics-history-chart-panel').forEach(panel => panel.remove());
    $('history-legacy-panel')?.classList.remove('hidden');
  }

  function closeHistoryModal() {
    $('history-modal')?.classList.add('hidden');
    $('history-modal')?.classList.remove('flex');

    if (historyRefreshInterval) {
      clearInterval(historyRefreshInterval);
      historyRefreshInterval = null;
    }

    historyRenderVersion += 1;
    metricsHistoryClient?.cancel();
    destroyHistoryCharts();
    setHistoryState('');
    setHistoryMode('');
    setHistoryDiagnostics('');
    setHistoryError('');
    activeHistoryRequest = null;
  }

  async function loadHistory(button) {
    const group = button.dataset.group;
    const id = button.dataset.id;
    const title = button.dataset.title;

    metricsHistoryClient?.cancel();
    activeHistoryRequest = {
      group,
      id,
      title,
      deviceName: button.dataset.device || '',
      sensorName: button.dataset.sensor || title || '',
      status: button.dataset.status || '',
      message: button.dataset.message || '',
      lastvalue: button.dataset.lastvalue || '',
      forceLegacy: false,
      fallbackMessage: ''
    };

    if ($('history-title')) {
      $('history-title').textContent = title || 'Histórico';
    }

    if ($('history-subtitle')) {
      $('history-subtitle').textContent = METRICS_UI_ENABLED
        ? `${group} · Sensor ${id} · ${METRICS_HISTORY_POINTS} puntos solicitados`
        : `${group} · Sensor ${id} · últimas ${HISTORY_MINUTES} min`;
    }

    await fetchAndRenderHistory({
      group,
      id,
      title,
      open: true
    });

    if (historyRefreshInterval) {
      clearInterval(historyRefreshInterval);
    }

    historyRefreshInterval = setInterval(() => {
      if (!activeHistoryRequest) return;

      fetchAndRenderHistory({
        ...activeHistoryRequest,
        open: false
      });
    }, REFRESH_MS);
  }

  function historyDatasetStyle(dataset) {
    const styles = {
      ping: {
        borderColor: '#e5e7eb',
        backgroundColor: 'rgba(229,231,235,.08)',
        borderWidth: 2,
        pointRadius: 2
      },
      minimum: {
        borderColor: '#22d3ee',
        backgroundColor: 'rgba(34,211,238,.08)',
        borderWidth: 2,
        pointRadius: 2
      },
      maximum: {
        borderColor: '#f472b6',
        backgroundColor: 'rgba(244,114,182,.08)',
        borderWidth: 2,
        pointRadius: 2
      },
      packet_loss: {
        borderColor: '#a3a33a',
        backgroundColor: 'rgba(163,163,58,.08)',
        borderWidth: 2,
        pointRadius: 2,
        borderDash: [5, 5]
      },
      downtime: {
        borderColor: '#ef4444',
        backgroundColor: 'rgba(239,68,68,.08)',
        borderWidth: 2,
        pointRadius: 2,
        borderDash: [8, 5]
      },
      coverage: {
        borderColor: '#818cf8',
        backgroundColor: 'rgba(129,140,248,.08)',
        borderWidth: 1,
        pointRadius: 1,
        borderDash: [3, 3],
        hidden: true
      }
    };

    return styles[dataset.key] || {
      borderWidth: 2,
      pointRadius: 2
    };
  }

  function normalizeHistoryValue(dataset, point) {
    let value = Number(point.value);

    if (!Number.isFinite(value)) {
      return null;
    }

    if (dataset.key === 'coverage' && value > 100) {
      value = value / 100;
    }

    return {
      x: point.t,
      y: value
    };
  }

  function buildHistoryChartDatasets(payload) {
    const rawDatasets = Array.isArray(payload.datasets) && payload.datasets.length
      ? payload.datasets
      : [{
          key: 'value',
          label: payload.unit ? `Valor (${payload.unit})` : 'Valor',
          unit: payload.unit || '',
          axis: payload.unit === '%' ? 'y1' : 'y',
          data: payload.series || []
        }];

    const order = ['ping', 'minimum', 'maximum', 'packet_loss', 'downtime', 'coverage'];

    return rawDatasets
      .filter(dataset => Array.isArray(dataset.data) && dataset.data.length)
      .sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key))
      .map(dataset => {
        const style = historyDatasetStyle(dataset);
        const unit = dataset.unit || '';
        const label = unit ? `${dataset.label} (${unit})` : dataset.label;

        return {
          label,
          data: dataset.data
            .map(point => normalizeHistoryValue(dataset, point))
            .filter(Boolean),
          yAxisID: dataset.axis || (unit === '%' ? 'y1' : 'y'),
          tension: 0.25,
          fill: false,
          spanGaps: true,
          ...style
        };
      });
  }

  async function fetchAndRenderLegacyHistory({ group, id }, requestVersion, fallbackMessage = '') {
    setHistoryMode(
      fallbackMessage ? `Modo legacy · ${fallbackMessage}` : 'Histórico legacy',
      true
    );
    try {
      const params = new URLSearchParams({
        group,
        id,
        minutes: String(HISTORY_MINUTES),
        avg: String(HISTORY_AVG_SECONDS)
      });

      const payload = await fetchJSON(`/api/history?${params.toString()}`, 1);
      if (requestVersion !== historyRenderVersion) return;
      const ctx = $('historyChart')?.getContext('2d');

      if (!ctx) return;

      const chartDatasets = buildHistoryChartDatasets(payload);

      if (!chartDatasets.length) {
        setHistoryState(
          payload.error || 'No hubo puntos históricos para este sensor en la ventana consultada.',
          'empty'
        );
        return;
      }

      $('history-legacy-panel')?.classList.remove('hidden');

      const data = {
        datasets: chartDatasets
      };

      const options = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false
        },
        plugins: {
          legend: {
            position: 'bottom',
            labels: {
              usePointStyle: true,
              boxWidth: 10
            }
          },
          tooltip: {
            callbacks: {
              label(context) {
                const label = context.dataset.label || '';
                const value = context.parsed.y;

                if (label.includes('%')) {
                  return `${label}: ${Number(value).toFixed(2)}%`;
                }

                if (label.toLowerCase().includes('ms')) {
                  return `${label}: ${Number(value).toFixed(2)} ms`;
                }

                return `${label}: ${Number(value).toFixed(2)}`;
              }
            }
          }
        },
        scales: {
          x: {
            type: 'time',
            time: {
              tooltipFormat: 'yyyy-MM-dd HH:mm',
              displayFormats: {
                minute: 'HH:mm',
                hour: 'HH:mm'
              }
            },
            ticks: {
              maxRotation: 55,
              minRotation: 35
            }
          },
          y: {
            type: 'linear',
            position: 'left',
            title: {
              display: true,
              text: 'ms'
            },
            beginAtZero: false
          },
          y1: {
            type: 'linear',
            position: 'right',
            title: {
              display: true,
              text: '%'
            },
            min: 0,
            max: 100,
            grid: {
              drawOnChartArea: false
            }
          }
        }
      };

      if (!historyChart) {
        historyChart = new Chart(ctx, {
          type: 'line',
          data,
          options
        });
      } else {
        historyChart.data = data;
        historyChart.options = options;
        historyChart.update('none');
      }

      if ($('history-subtitle')) {
        $('history-subtitle').textContent = `${group} · Sensor ${id} · últimas ${HISTORY_MINUTES} min · auto-refresh ${REFRESH_MS / 1000}s`;
      }
      setHistoryState('Histórico legacy cargado.', 'success');
    } catch (error) {
      if (requestVersion !== historyRenderVersion) return;
      setHistoryState('');
      setHistoryError(`No se pudo cargar el histórico. ${error.message}`);
    }
  }

  function renderNormalizedHistory(payload, group, id) {
    const chartGrid = $('history-chart-grid');
    if (!chartGrid || typeof Chart === 'undefined') return false;
    const chartGroups = METRICS_HISTORY.buildChartGroups(payload);
    if (!chartGroups.length) return false;

    $('history-legacy-panel')?.classList.add('hidden');
    for (const groupDefinition of chartGroups) {
      const panel = document.createElement('section');
      panel.className = 'history-chart-panel metrics-history-chart-panel';

      const heading = document.createElement('h4');
      heading.className = 'history-chart-heading';
      heading.textContent = `${groupDefinition.title} (${groupDefinition.unit})`;

      const canvasBox = document.createElement('div');
      canvasBox.className = 'history-chart-canvas';
      const canvas = document.createElement('canvas');
      canvasBox.appendChild(canvas);
      panel.appendChild(heading);
      panel.appendChild(canvasBox);
      chartGrid.appendChild(panel);

      normalizedHistoryCharts.push(
        new Chart(canvas.getContext('2d'), METRICS_HISTORY.chartConfig(groupDefinition))
      );
    }

    const diagnostics = METRICS_HISTORY.diagnosticState(payload);
    const diagnosticParts = [];
    if (diagnostics.missing.length) {
      diagnosticParts.push(`${diagnostics.missing.length} canal(es) opcional(es) no disponible(s)`);
    }
    if (diagnostics.issues) {
      diagnosticParts.push(`${diagnostics.issues} issue(s) de fuente`);
    }
    if (diagnostics.unknown) {
      diagnosticParts.push(`${diagnostics.unknown} canal(es) desconocido(s)`);
    }
    setHistoryDiagnostics(diagnosticParts.join(' · '));
    setHistoryMode(
      diagnostics.partial ? 'Métricas v2 · datos parciales' : 'Métricas v2',
      false
    );
    setHistoryState(
      diagnostics.partial
        ? 'Se muestran todos los canales válidos disponibles.'
        : 'Histórico multicanal cargado.',
      diagnostics.partial ? 'partial' : 'success'
    );
    if ($('history-subtitle')) {
      const observedRange = METRICS_HISTORY.formatObservedRange(payload.range);
      $('history-subtitle').textContent = `${group} · Sensor ${id} · ${payload.sensor.sensor_type.toUpperCase()} · ${observedRange} · auto-refresh ${REFRESH_MS / 1000}s`;
    }
    return true;
  }

  async function fetchAndRenderHistory(request) {
    const { group, id, open = false } = request;
    const requestVersion = ++historyRenderVersion;
    if (open) openHistoryModal();

    setHistoryError('');
    setHistoryDiagnostics('');
    setHistoryMode('');
    setHistoryState('Cargando histórico…', 'loading');
    destroyHistoryCharts();

    if (METRICS_UI_ENABLED && !metricsHistoryClient) {
      setHistoryState('');
      setHistoryError('La integración de métricas v2 no está disponible en esta página.');
      return;
    }

    const useMetricsV2 = Boolean(
      METRICS_UI_ENABLED &&
      metricsHistoryClient &&
      !request.forceLegacy
    );
    if (!useMetricsV2) {
      await fetchAndRenderLegacyHistory(
        { group, id },
        requestVersion,
        request.fallbackMessage || (METRICS_UI_ENABLED ? 'sensor no compatible con v2' : '')
      );
      return;
    }

    try {
      const result = await metricsHistoryClient.load({
        server: group,
        sensorId: id,
        points: METRICS_HISTORY_POINTS
      });
      if (requestVersion !== historyRenderVersion) return;
      if (!renderNormalizedHistory(result.history, group, id)) {
        setHistoryState('No hay canales válidos para representar.', 'empty');
      }
    } catch (error) {
      if (METRICS_HISTORY.isCancelledRequest(error) || requestVersion !== historyRenderVersion) {
        return;
      }
      if (METRICS_HISTORY.shouldFallbackToLegacy(error)) {
        const fallbackMessage = error.code === 'sensor_not_found'
          ? 'sensor sin histórico normalizado'
          : 'provider v2 no disponible';
        if (activeHistoryRequest) {
          activeHistoryRequest.forceLegacy = true;
          activeHistoryRequest.fallbackMessage = fallbackMessage;
        }
        await fetchAndRenderLegacyHistory(
          { group, id },
          requestVersion,
          fallbackMessage
        );
        return;
      }
      setHistoryState('');
      setHistoryError(`No se pudo cargar el histórico multicanal. ${error.message}`);
    }
  }

  function toggleHistoryPauseMenu(force) {
    const menu = $('history-pause-menu');

    if (!menu) return;

    const shouldOpen = typeof force === 'boolean'
      ? force
      : menu.classList.contains('hidden');

    menu.classList.toggle('hidden', !shouldOpen);
  }
  function isSensorPaused(sensorOrStatus) {
  if (typeof sensorOrStatus === 'object' && sensorOrStatus !== null) {
    const text = [
      sensorOrStatus.status,
      sensorOrStatus.message,
      sensorOrStatus.lastvalue,
      sensorOrStatus.status_text,
      sensorOrStatus.status_raw
    ].map(value => String(value || '').toLowerCase()).join(' ');

    return (
      text.includes('paused') ||
      text.includes('pause') ||
      text.includes('pausado') ||
      text.includes('pausada') ||
      text.includes('pausar')
    );
  }

  const status = String(sensorOrStatus || '').toLowerCase();

  return (
    status.includes('paused') ||
    status.includes('pause') ||
    status.includes('pausado') ||
    status.includes('pausada')
  );
}

function sensorTargetFromDataset(dataset = {}) {
  return {
    group: dataset.group || '',
    id: dataset.id || '',
    title: dataset.title || '',
    deviceName: dataset.device || '',
    sensorName: dataset.sensor || dataset.title || '',
    status: dataset.status || '',
    message: dataset.message || '',
    lastvalue: dataset.lastvalue || ''
  };
}

function setPauseUntilMinNow() {
  const input = $('pause-until-input');
  if (!input) return;

  const now = new Date();
  now.setMinutes(now.getMinutes() + 5);
  now.setSeconds(0, 0);

  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);

  input.min = local;

  if (!input.value) {
    input.value = local;
  }
}

function openSensorPauseModal(target) {
  activePauseRequest = target;

  if ($('pause-modal-subtitle')) {
    $('pause-modal-subtitle').textContent = `${target.deviceName || 'Dispositivo'} · ${target.sensorName || target.title || target.id}`;
  }

  if ($('pause-message-input')) {
    $('pause-message-input').value = '';
  }

  setPauseUntilMinNow();

  const paused = isSensorPaused(target.status);

  $('pause-modal-resume-btn')?.classList.toggle('hidden', !paused);

  openModal('sensor-pause-modal');
}

function closeSensorPauseModal() {
  closeModal('sensor-pause-modal');
}

function pauseMessage() {
  const custom = String($('pause-message-input')?.value || '').trim();

  if (custom) return custom;

  return `Pausado desde dashboard por ${USERNAME}`;
}

async function pauseTarget(payload) {
  if (!activePauseRequest) {
    toastr.warning('No hay sensor seleccionado');
    return;
  }

  try {
    const response = await requestJSON('/api/sensors/pause', {
      method: 'POST',
      body: {
        group: activePauseRequest.group,
        id: activePauseRequest.id,
        device_name: activePauseRequest.deviceName || '',
        sensor_name: activePauseRequest.sensorName || activePauseRequest.title || '',
        message: pauseMessage(),
        ...payload
      }
    });

    toastr.success(response.message || 'Sensor pausado');
    closeSensorPauseModal();

    await checkForUpdates();

    if (activeHistoryRequest) {
      fetchAndRenderHistory({
        ...activeHistoryRequest,
        open: false
      });
    }
  } catch (error) {
    toastr.error(error.message, 'No se pudo pausar');
  }
}

async function pauseTargetForMinutes(minutes) {
  const label = Number(minutes) >= 1440
    ? '24 horas'
    : Number(minutes) >= 180
      ? '3 horas'
      : '1 hora';

  const ok = confirm(`¿Seguro que quieres pausar este sensor por ${label}?`);

  if (!ok) return;

  await pauseTarget({
    duration_minutes: Number(minutes)
  });
}

async function pauseTargetUntil() {
  const pauseUntil = String($('pause-until-input')?.value || '').trim();

  if (!pauseUntil) {
    toastr.warning('Selecciona fecha/hora');
    return;
  }

  const ok = confirm('¿Seguro que quieres pausar este sensor hasta esa fecha/hora?');

  if (!ok) return;

  await pauseTarget({
    pause_until: pauseUntil
  });
}

async function pauseTargetIndefinitely() {
  const ok = confirm('¿Seguro que quieres pausar este sensor indefinidamente?');

  if (!ok) return;

  await pauseTarget({
    indefinite: true
  });
}

async function resumeSensorTarget(target = null) {
  const item = target || activePauseRequest || activeHistoryRequest;

  if (!item) {
    toastr.warning('No hay sensor seleccionado');
    return;
  }

  const ok = confirm('¿Seguro que quieres reanudar este sensor?');

  if (!ok) return;

  try {
    const response = await requestJSON('/api/sensors/resume', {
      method: 'POST',
      body: {
        group: item.group,
        id: item.id,
        device_name: item.deviceName || '',
        sensor_name: item.sensorName || item.title || ''
      }
    });

    toastr.success(response.message || 'Sensor reanudado');
    closeSensorPauseModal();

    await checkForUpdates();

    if (activeHistoryRequest) {
      fetchAndRenderHistory({
        ...activeHistoryRequest,
        open: false
      });
    }
  } catch (error) {
    toastr.error(error.message, 'No se pudo reanudar');
  }
}


  async function pauseActiveHistorySensor(durationMinutes) {
    if (!activeHistoryRequest) {
      toastr.warning('No hay sensor activo para pausar');
      return;
    }

    const minutes = Number(durationMinutes);

    if (!Number.isFinite(minutes) || minutes <= 0) {
      toastr.warning('Duración inválida');
      return;
    }

    const label = minutes >= 1440
      ? '24 horas'
      : minutes >= 180
        ? '3 horas'
        : '1 hora';

    const ok = confirm(`¿Seguro que quieres pausar este sensor por ${label}?`);

    if (!ok) return;

    toggleHistoryPauseMenu(false);

    try {
      const payload = await requestJSON('/api/sensors/pause', {
        method: 'POST',
        body: {
          group: activeHistoryRequest.group,
          id: activeHistoryRequest.id,
          duration_minutes: minutes,
          device_name: activeHistoryRequest.deviceName || '',
          sensor_name: activeHistoryRequest.sensorName || activeHistoryRequest.title || '',
          message: `Pausado desde dashboard por ${USERNAME}`
        }
      });

      toastr.success(payload.message || 'Sensor pausado');

      checkForUpdates();

      fetchAndRenderHistory({
        ...activeHistoryRequest,
        open: false
      });
    } catch (error) {
      toastr.error(error.message, 'No se pudo pausar');
    }
  }

  async function resumeActiveHistorySensor() {
    if (!activeHistoryRequest) {
      toastr.warning('No hay sensor activo para reanudar');
      return;
    }

    const ok = confirm('¿Seguro que quieres reanudar este sensor?');

    if (!ok) return;

    toggleHistoryPauseMenu(false);

    try {
      const payload = await requestJSON('/api/sensors/resume', {
        method: 'POST',
        body: {
          group: activeHistoryRequest.group,
          id: activeHistoryRequest.id,
          device_name: activeHistoryRequest.deviceName || '',
          sensor_name: activeHistoryRequest.sensorName || activeHistoryRequest.title || ''
        }
      });

      toastr.success(payload.message || 'Sensor reanudado');

      checkForUpdates();

      fetchAndRenderHistory({
        ...activeHistoryRequest,
        open: false
      });
    } catch (error) {
      toastr.error(error.message, 'No se pudo reanudar');
    }
  }

  function bindSensorGridActions(gridId) {
  $(gridId)?.addEventListener('click', event => {
    const pinButton = event.target.closest('.sensor-pin-btn');
    const pauseButton = event.target.closest('.sensor-pause-btn');
    const historyButton = event.target.closest('.history-btn');

    if (pinButton) {
      toggleSensorPin(pinButton);
      return;
    }

    if (pauseButton) {
      const target = sensorTargetFromDataset(pauseButton.dataset);

      if (pauseButton.dataset.action === 'resume') {
        resumeSensorTarget(target);
      } else {
        openSensorPauseModal(target);
      }

      return;
    }

    if (historyButton) {
      loadHistory(historyButton);
    }
  });
}

  function bindEvents() {
    $('retry-btn')?.addEventListener('click', loadData);

    $('settings-toggle')?.addEventListener('click', (event) => {
      event.stopPropagation();
      toggleSettingsMenu();
    });
    $('pause-modal-close')?.addEventListener('click', closeSensorPauseModal);

$('sensor-pause-modal')?.addEventListener('click', (event) => {
  if (event.target === $('sensor-pause-modal')) {
    closeSensorPauseModal();
  }
});

document.querySelectorAll('.pause-quick-option').forEach(button => {
  button.addEventListener('click', () => {
    pauseTargetForMinutes(button.dataset.minutes);
  });
});

$('pause-indefinite-btn')?.addEventListener('click', pauseTargetIndefinitely);
$('apply-pause-until-btn')?.addEventListener('click', pauseTargetUntil);
$('pause-modal-resume-btn')?.addEventListener('click', () => resumeSensorTarget(activePauseRequest));
    $('settings-logout')?.addEventListener('click', () => {
  sessionStorage.removeItem('dashboard_welcome_seen');
   });

    $('settings-servers')?.addEventListener('click', openServerModal);
    $('settings-users')?.addEventListener('click', openUserModal);
    $('settings-alerts')?.addEventListener('click', openAlertsModal);
    $('settings-audit')?.addEventListener('click', openAuditModal);

    document.addEventListener('click', (event) => {
      if (!$('settings-fab')?.contains(event.target)) {
        toggleSettingsMenu(false);
      }
    });

    $('history-close')?.addEventListener('click', closeHistoryModal);

    $('history-modal')?.addEventListener('click', (event) => {
      if (event.target === $('history-modal')) {
        closeHistoryModal();
      }
    });

   $('history-pause-toggle')?.addEventListener('click', (event) => {
  event.stopPropagation();

  if (!activeHistoryRequest) {
    toastr.warning('No hay sensor activo');
    return;
  }

  openSensorPauseModal(activeHistoryRequest);
});

    $('history-resume-btn')?.addEventListener('click', resumeActiveHistorySensor);

    $('history-pause-menu')?.addEventListener('click', (event) => {
      const option = event.target.closest('.history-pause-option');

      if (!option) return;

      const minutes = option.dataset.minutes;

      if (minutes) {
        pauseActiveHistorySensor(minutes);
        return;
      }

      if (option.id === 'history-custom-pause') {
        toastr.info('Horario personalizado viene en la siguiente vuelta, papito');
        toggleHistoryPauseMenu(false);
      }
    });

    document.addEventListener('click', (event) => {
      const menu = $('history-pause-menu');
      const toggle = $('history-pause-toggle');

      if (!menu || !toggle) return;

      if (!menu.contains(event.target) && !toggle.contains(event.target)) {
        toggleHistoryPauseMenu(false);
      }
    });

    $('server-close')?.addEventListener('click', () => closeModal('server-modal'));

    $('server-modal')?.addEventListener('click', (event) => {
      if (event.target === $('server-modal')) {
        closeModal('server-modal');
      }
    });

    $('user-close')?.addEventListener('click', () => closeModal('user-modal'));

    $('user-modal')?.addEventListener('click', (event) => {
      if (event.target === $('user-modal')) {
        closeModal('user-modal');
      }
    });

    $('alerts-close')?.addEventListener('click', () => closeModal('alerts-modal'));

    $('alerts-modal')?.addEventListener('click', (event) => {
      if (event.target === $('alerts-modal')) {
        closeModal('alerts-modal');
      }
    });

    $('audit-close')?.addEventListener('click', () => closeModal('audit-modal'));

    $('audit-modal')?.addEventListener('click', (event) => {
      if (event.target === $('audit-modal')) {
        closeModal('audit-modal');
      }
    });

    $('audit-filter-form')?.addEventListener('submit', (event) => {
      event.preventDefault();
      loadAuditLogs();
    });

    $('audit-reset-filters')?.addEventListener('click', resetAuditFilters);
    $('audit-refresh')?.addEventListener('click', loadAuditLogs);

    $('test-alert-sound')?.addEventListener('click', () => {
      unlockAudio();
      playDefaultAlertSound({ force: true });
    });

    $('save-alert-settings')?.addEventListener('click', saveAlertModalValues);
    $('upload-alert-sound')?.addEventListener('click', uploadAlertSound);
    $('reset-alert-sound')?.addEventListener('click', resetAlertSound);

    $('server-form')?.addEventListener('submit', submitServerForm);
    $('user-form')?.addEventListener('submit', submitUserForm);

    $('servers-list')?.addEventListener('click', (event) => {
      const toggleButton = event.target.closest('.toggle-server-btn');
      const deleteButton = event.target.closest('.delete-server-btn');

      if (toggleButton) {
        toggleServerEnabled(toggleButton);
        return;
      }

      if (deleteButton) {
        deleteServer(deleteButton);
      }
    });

    $('users-list')?.addEventListener('change', (event) => {
      const select = event.target.closest('.user-role-select');

      if (select) {
        updateUserRole(select);
      }
    });

    $('users-list')?.addEventListener('click', (event) => {
      const deleteButton = event.target.closest('.delete-user-btn');
      const toggleButton = event.target.closest('.toggle-user-active-btn');
      const resetButton = event.target.closest('.reset-password-btn');

      if (deleteButton) {
        deleteUser(deleteButton);
        return;
      }

      if (toggleButton) {
        toggleUserActive(toggleButton);
        return;
      }

      if (resetButton) {
        resetUserPassword(resetButton);
      }
    });

    document.querySelectorAll('.tab-button').forEach(button => {
      button.addEventListener('click', () => setView(button.dataset.view));
    });

    document.querySelectorAll('.log-export-btn').forEach(button => {
      button.addEventListener('click', () => {
        exportSensorLogs(button.dataset.hours, button);
      });
    });

    let searchTimer = null;

    $('search')?.addEventListener('input', () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(() => renderAll(), 200);
    });

    ['status-filter', 'server-filter'].forEach(id => {
      $(id)?.addEventListener('change', () => renderAll());
    });
    bindSensorGridActions('sensor-grid');
    bindSensorGridActions('pinned-sensor-grid');

    document.addEventListener('click', unlockAudio, { once: true });
    document.addEventListener('keydown', unlockAudio, { once: true });
  }

  document.addEventListener('DOMContentLoaded', () => {
  console.log(`Rol detectado por dashboard.js: ${USER_ROLE}`);

  // Dashboard fijo en modo oscuro
  document.documentElement.classList.add('dark');
  localStorage.setItem('theme', 'dark');

  bindEvents();
  setView('sensors');

  const welcomeBar = $('welcome-bar');

  if (welcomeBar) {
    const alreadySeen = sessionStorage.getItem('dashboard_welcome_seen') === '1';

    if (alreadySeen) {
      welcomeBar.classList.add('is-hidden', 'welcome-bar-no-animation');
    } else {
      setTimeout(() => {
        welcomeBar.classList.add('is-hidden');
        sessionStorage.setItem('dashboard_welcome_seen', '1');
      }, 7000);
    }
  }

  Promise.allSettled([
  fetchAlertSettingsFromServer(),
  loadPinnedSensors()
]).finally(() => {
  loadData();
});
});
})();
