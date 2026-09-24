(function exposeMetricsHistory(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) {
    module.exports = api;
  }
  if (root) {
    root.CoreViewMetricsHistory = api;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, function metricsHistoryFactory() {
  'use strict';

  const SUPPORTED_SENSOR_TYPES = Object.freeze([
    'ping',
    'cpu',
    'memory',
    'traffic',
    'sfp'
  ]);

  const EXPECTED_CHANNELS = Object.freeze({
    ping: ['latency_ms', 'packet_loss_pct', 'availability_pct'],
    cpu: ['cpu_usage_pct'],
    memory: ['memory_usage_pct', 'memory_used_bytes'],
    traffic: ['traffic_in_bps', 'traffic_out_bps', 'utilization_pct'],
    sfp: [
      'rx_power_dbm',
      'tx_power_dbm',
      'temperature_c',
      'voltage_v',
      'bias_current_ma'
    ]
  });

  const SENSOR_GROUPS = Object.freeze({
    ping: [
      { key: 'latency', title: 'Latencia', unit: 'ms', channels: ['latency_ms'] },
      {
        key: 'ping_quality',
        title: 'Disponibilidad y pérdida',
        unit: '%',
        channels: ['packet_loss_pct', 'availability_pct']
      }
    ],
    cpu: [
      { key: 'cpu', title: 'Utilización CPU', unit: '%', channels: ['cpu_usage_pct'] }
    ],
    memory: [
      {
        key: 'memory_usage',
        title: 'Utilización de memoria',
        unit: '%',
        channels: ['memory_usage_pct']
      },
      {
        key: 'memory_bytes',
        title: 'Memoria utilizada',
        unit: 'bytes',
        channels: ['memory_used_bytes']
      }
    ],
    traffic: [
      {
        key: 'traffic_rate',
        title: 'Tráfico IN / OUT',
        unit: 'bps',
        channels: ['traffic_in_bps', 'traffic_out_bps']
      },
      {
        key: 'traffic_utilization',
        title: 'Utilización del enlace',
        unit: '%',
        channels: ['utilization_pct']
      }
    ],
    sfp: [
      {
        key: 'optical_power',
        title: 'Potencia óptica',
        unit: 'dBm',
        channels: ['rx_power_dbm', 'tx_power_dbm']
      },
      {
        key: 'temperature',
        title: 'Temperatura',
        unit: '°C',
        channels: ['temperature_c']
      },
      {
        key: 'voltage',
        title: 'Voltaje',
        unit: 'V',
        channels: ['voltage_v']
      },
      {
        key: 'bias_current',
        title: 'Corriente de bias',
        unit: 'mA',
        channels: ['bias_current_ma']
      }
    ]
  });

  const CHANNEL_COLORS = Object.freeze({
    latency_ms: '#22d3ee',
    packet_loss_pct: '#f97316',
    availability_pct: '#22c55e',
    cpu_usage_pct: '#a78bfa',
    memory_usage_pct: '#e879f9',
    memory_used_bytes: '#38bdf8',
    traffic_in_bps: '#22c55e',
    traffic_out_bps: '#f59e0b',
    utilization_pct: '#60a5fa',
    rx_power_dbm: '#2dd4bf',
    tx_power_dbm: '#fb7185',
    temperature_c: '#f97316',
    voltage_v: '#facc15',
    bias_current_ma: '#c084fc'
  });

  class MetricsAPIError extends Error {
    constructor(message, { status = 0, code = 'request_failed' } = {}) {
      super(message || 'No se pudo consultar métricas.');
      this.name = 'MetricsAPIError';
      this.status = Number(status) || 0;
      this.code = String(code || 'request_failed');
    }
  }

  class MetricsContractError extends Error {
    constructor(message) {
      super(message);
      this.name = 'MetricsContractError';
      this.code = 'invalid_contract';
    }
  }

  class StaleMetricsRequestError extends Error {
    constructor() {
      super('La respuesta pertenece a una selección anterior.');
      this.name = 'StaleMetricsRequestError';
      this.code = 'stale_request';
    }
  }

  function requiredText(value, name) {
    const text = String(value == null ? '' : value).trim();
    if (!text) throw new MetricsContractError(`${name} es obligatorio`);
    return text;
  }

  function buildMetadataURL({ server, sensorId }) {
    const params = new URLSearchParams({
      server: requiredText(server, 'server'),
      sensor_id: requiredText(sensorId, 'sensor_id')
    });
    return `/api/v2/metrics/sensor-metadata?${params.toString()}`;
  }

  function buildHistoryURL({ server, sensorId, sensorType, points, start, end }) {
    const params = new URLSearchParams({
      server: requiredText(server, 'server'),
      sensor_id: requiredText(sensorId, 'sensor_id'),
      sensor_type: requiredText(sensorType, 'sensor_type')
    });
    const hasPoints = points !== undefined && points !== null;
    const hasStart = start !== undefined && start !== null && String(start).trim() !== '';
    const hasEnd = end !== undefined && end !== null && String(end).trim() !== '';
    if (hasPoints === (hasStart || hasEnd)) {
      throw new MetricsContractError('Usa points o start/end, pero no ambos');
    }
    if (hasPoints) {
      const count = Number(points);
      if (!Number.isInteger(count) || count < 1 || count > 1000) {
        throw new MetricsContractError('points debe estar entre 1 y 1000');
      }
      params.set('points', String(count));
    } else {
      if (!hasStart || !hasEnd) {
        throw new MetricsContractError('start y end deben proporcionarse juntos');
      }
      params.set('start', String(start));
      params.set('end', String(end));
    }
    return `/api/v2/metrics/history?${params.toString()}`;
  }

  function validateMetadata(payload, expectedSensorId) {
    const sensor = payload && payload.schema_version === '2.0' ? payload.sensor : null;
    if (!sensor || String(sensor.sensor_id) !== String(expectedSensorId)) {
      throw new MetricsContractError('Metadata de sensor inválida');
    }
    const sensorType = String(sensor.sensor_type || '').toLowerCase();
    if (!SUPPORTED_SENSOR_TYPES.includes(sensorType)) {
      throw new MetricsContractError('Tipo de sensor no compatible');
    }
    return {
      serverId: requiredText(sensor.server_id, 'server_id'),
      sensorId: requiredText(sensor.sensor_id, 'sensor_id'),
      sensorType
    };
  }

  function validateHistoryPayload(payload, metadata) {
    if (!payload || payload.schema_version !== '2.0' || !payload.sensor) {
      throw new MetricsContractError('Respuesta histórica v2 inválida');
    }
    if (
      String(payload.sensor.server_id) !== metadata.serverId ||
      String(payload.sensor.sensor_id) !== metadata.sensorId ||
      String(payload.sensor.sensor_type).toLowerCase() !== metadata.sensorType
    ) {
      throw new MetricsContractError('Identidad histórica v2 inconsistente');
    }
    if (!payload.channels || typeof payload.channels !== 'object' || Array.isArray(payload.channels)) {
      throw new MetricsContractError('Canales históricos v2 inválidos');
    }
    for (const [channelKey, channel] of Object.entries(payload.channels)) {
      if (!channel || !Array.isArray(channel.samples) || !String(channel.unit || '')) {
        throw new MetricsContractError(`Canal inválido: ${channelKey}`);
      }
      let previousTimestamp = -Infinity;
      for (const sample of channel.samples) {
        const timestamp = sample && String(sample.timestamp || '');
        const parsedTimestamp = Date.parse(timestamp);
        const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp);
        if (!sample || !Number.isFinite(sample.value) || !hasTimezone || !Number.isFinite(parsedTimestamp)) {
          throw new MetricsContractError(`Muestra inválida: ${channelKey}`);
        }
        if (parsedTimestamp < previousTimestamp) {
          throw new MetricsContractError(`Serie desordenada: ${channelKey}`);
        }
        previousTimestamp = parsedTimestamp;
      }
    }
    return payload;
  }

  async function responseJSON(fetchImpl, url, signal) {
    let response;
    try {
      response = await fetchImpl(url, {
        method: 'GET',
        cache: 'no-store',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal
      });
    } catch (error) {
      if (error && error.name === 'AbortError') throw error;
      throw new MetricsAPIError('No se pudo conectar con la API de métricas.');
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new MetricsAPIError(
        payload.error || `HTTP ${response.status}`,
        { status: response.status, code: payload.code }
      );
    }
    return payload;
  }

  function createClient({ fetchImpl, AbortControllerImpl } = {}) {
    const selectedFetch = fetchImpl || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
    const SelectedAbortController = AbortControllerImpl || globalThis.AbortController;
    if (typeof selectedFetch !== 'function') {
      throw new MetricsContractError('fetch no está disponible');
    }
    const metadataCache = new Map();
    let generation = 0;
    let activeController = null;

    function cancel() {
      generation += 1;
      if (activeController) activeController.abort();
      activeController = null;
    }

    async function load(options) {
      cancel();
      const requestGeneration = generation;
      activeController = SelectedAbortController ? new SelectedAbortController() : null;
      const signal = activeController ? activeController.signal : undefined;
      const server = requiredText(options.server, 'server');
      const sensorId = requiredText(options.sensorId, 'sensor_id');
      const cacheKey = JSON.stringify([server, sensorId]);
      let metadata = metadataCache.get(cacheKey);
      if (!metadata) {
        const payload = await responseJSON(
          selectedFetch,
          buildMetadataURL({ server, sensorId }),
          signal
        );
        if (generation !== requestGeneration) throw new StaleMetricsRequestError();
        metadata = validateMetadata(payload, sensorId);
        metadataCache.set(cacheKey, metadata);
      }
      const history = await responseJSON(
        selectedFetch,
        buildHistoryURL({
          server,
          sensorId,
          sensorType: metadata.sensorType,
          points: options.points,
          start: options.start,
          end: options.end
        }),
        signal
      );
      if (generation !== requestGeneration) throw new StaleMetricsRequestError();
      return { metadata, history: validateHistoryPayload(history, metadata) };
    }

    return {
      load,
      cancel,
      clearMetadataCache() {
        metadataCache.clear();
      }
    };
  }

  function shouldFallbackToLegacy(error) {
    if (!(error instanceof MetricsAPIError)) return false;
    if (error.status === 502 && error.code === 'upstream_error') return true;
    return error.status === 404 && ['server_not_found', 'sensor_not_found'].includes(error.code);
  }

  function isCancelledRequest(error) {
    return Boolean(
      error &&
      (error.name === 'AbortError' || error instanceof StaleMetricsRequestError)
    );
  }

  function formatScaled(value, unit) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return 'N/A';
    const absolute = Math.abs(numeric);
    if (unit === 'bps') {
      if (absolute >= 1e9) return `${(numeric / 1e9).toFixed(2)} Gbps`;
      if (absolute >= 1e6) return `${(numeric / 1e6).toFixed(2)} Mbps`;
      if (absolute >= 1e3) return `${(numeric / 1e3).toFixed(2)} Kbps`;
      return `${numeric.toFixed(0)} bps`;
    }
    if (unit === 'bytes') {
      if (absolute >= 1e9) return `${(numeric / 1e9).toFixed(2)} GB`;
      if (absolute >= 1e6) return `${(numeric / 1e6).toFixed(2)} MB`;
      if (absolute >= 1e3) return `${(numeric / 1e3).toFixed(2)} KB`;
      return `${numeric.toFixed(0)} bytes`;
    }
    if (unit === '%') return `${numeric.toFixed(2)}%`;
    return `${numeric.toFixed(2)} ${unit}`.trim();
  }

  function buildChartGroups(payload) {
    const sensorType = String(payload.sensor.sensor_type || '').toLowerCase();
    const definitions = SENSOR_GROUPS[sensorType];
    if (!definitions) throw new MetricsContractError('Tipo de sensor no compatible');
    return definitions.map(group => {
      const datasets = group.channels
        .filter(channelKey => Object.prototype.hasOwnProperty.call(payload.channels, channelKey))
        .map(channelKey => {
          const channel = payload.channels[channelKey];
          if (channel.unit !== group.unit) {
            throw new MetricsContractError(`Unidad incompatible para ${channelKey}`);
          }
          return {
            channelKey,
            label: String(channel.label || channelKey),
            unit: channel.unit,
            borderColor: CHANNEL_COLORS[channelKey] || '#94a3b8',
            backgroundColor: CHANNEL_COLORS[channelKey] || '#94a3b8',
            data: channel.samples.map(sample => ({
              x: sample.timestamp,
              y: sample.value
            }))
          };
        });
      return { ...group, datasets };
    }).filter(group => group.datasets.length > 0);
  }

  function diagnosticState(payload) {
    const issues = Array.isArray(payload.issues) ? payload.issues.length : 0;
    const unknown = Array.isArray(payload.unknown_channels) ? payload.unknown_channels.length : 0;
    const sensorType = String(payload.sensor.sensor_type || '').toLowerCase();
    const present = new Set(Object.keys(payload.channels || {}));
    const missing = (EXPECTED_CHANNELS[sensorType] || []).filter(key => !present.has(key));
    return {
      issues,
      unknown,
      missing,
      partial: issues > 0 || unknown > 0 || missing.length > 0
    };
  }

  function chartConfig(group) {
    return {
      type: 'line',
      data: {
        datasets: group.datasets.map(dataset => ({
          ...dataset,
          tension: 0.25,
          fill: false,
          pointRadius: 2,
          borderWidth: 2,
          spanGaps: true
        }))
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { position: 'bottom', labels: { usePointStyle: true, boxWidth: 10 } },
          tooltip: {
            callbacks: {
              label(context) {
                return `${context.dataset.label}: ${formatScaled(context.parsed.y, group.unit)}`;
              }
            }
          }
        },
        scales: {
          x: {
            type: 'time',
            time: {
              tooltipFormat: 'yyyy-MM-dd HH:mm',
              displayFormats: { minute: 'HH:mm', hour: 'HH:mm' }
            },
            ticks: { maxRotation: 55, minRotation: 35 }
          },
          y: {
            type: 'linear',
            position: 'left',
            title: { display: true, text: group.unit },
            beginAtZero: group.unit === '%',
            suggestedMin: group.unit === '%' ? 0 : undefined,
            suggestedMax: group.unit === '%' ? 100 : undefined,
            ticks: {
              callback(value) {
                return formatScaled(value, group.unit);
              }
            }
          }
        }
      }
    };
  }

  function destroyChartInstances(instances) {
    for (const chart of instances || []) {
      if (chart && typeof chart.destroy === 'function') chart.destroy();
    }
    if (Array.isArray(instances)) instances.length = 0;
  }

  function formatObservedRange(range, locale = 'es-MX') {
    if (!range || !range.start || !range.end) return 'sin rango observado';
    const format = value => new Date(value).toLocaleString(locale, {
      dateStyle: 'short',
      timeStyle: 'short'
    });
    return `${format(range.start)} – ${format(range.end)}`;
  }

  return Object.freeze({
    MetricsAPIError,
    MetricsContractError,
    StaleMetricsRequestError,
    SUPPORTED_SENSOR_TYPES,
    buildMetadataURL,
    buildHistoryURL,
    validateMetadata,
    validateHistoryPayload,
    createClient,
    shouldFallbackToLegacy,
    isCancelledRequest,
    formatScaled,
    buildChartGroups,
    diagnosticState,
    chartConfig,
    destroyChartInstances,
    formatObservedRange
  });
}));
