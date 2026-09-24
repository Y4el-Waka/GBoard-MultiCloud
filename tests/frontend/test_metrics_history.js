'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const metrics = require('../../static/metrics-history.js');

function channel(label, unit, values = [1, 2]) {
  return {
    label,
    unit,
    semantic: 'gauge',
    value_type: 'number',
    samples: values.map((value, index) => ({
      timestamp: `2026-09-23T12:${String(index * 5).padStart(2, '0')}:00Z`,
      value
    }))
  };
}

function payload(sensorType, channels, extras = {}) {
  return {
    schema_version: '2.0',
    sensor: {
      server_id: 'srv-prtg-01',
      sensor_id: '100001',
      sensor_type: sensorType
    },
    range: {
      start: '2026-09-23T12:00:00Z',
      end: '2026-09-23T12:05:00Z'
    },
    channels,
    issues: [],
    unknown_channels: [],
    ...extras
  };
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; }
  };
}

test('builds encoded metadata and points URLs without mixing modes', () => {
  const metadataURL = metrics.buildMetadataURL({
    server: 'PRTG LAB 01',
    sensorId: 100001
  });
  const historyURL = metrics.buildHistoryURL({
    server: 'PRTG LAB 01',
    sensorId: 100001,
    sensorType: 'traffic',
    points: 24
  });
  assert.match(metadataURL, /server=PRTG\+LAB\+01/);
  assert.match(historyURL, /sensor_type=traffic/);
  assert.match(historyURL, /points=24/);
  assert.doesNotMatch(historyURL, /start=/);
});

test('builds aware range URLs and rejects ambiguous parameters', () => {
  const url = metrics.buildHistoryURL({
    server: 'PRTG LAB 01',
    sensorId: '100001',
    sensorType: 'ping',
    start: '2026-09-23T12:00:00Z',
    end: '2026-09-23T13:00:00Z'
  });
  assert.match(url, /start=2026-09-23T12%3A00%3A00Z/);
  assert.throws(() => metrics.buildHistoryURL({
    server: 'PRTG LAB 01', sensorId: 1, sensorType: 'ping', points: 4,
    start: '2026-09-23T12:00:00Z', end: '2026-09-23T13:00:00Z'
  }), /points o start\/end/);
  assert.throws(() => metrics.buildHistoryURL({
    server: 'PRTG LAB 01', sensorId: 1, sensorType: 'ping', points: 0
  }), /points/);
});

test('client resolves explicit metadata then requests history with session credentials', async () => {
  const calls = [];
  const history = payload('cpu', { cpu_usage_pct: channel('CPU usage', '%', [30, 40]) });
  const responses = [
    jsonResponse({
      schema_version: '2.0',
      sensor: { server_id: 'srv-prtg-01', sensor_id: '100001', sensor_type: 'cpu' }
    }),
    jsonResponse(history),
    jsonResponse(history)
  ];
  const client = metrics.createClient({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return responses.shift();
    }
  });
  const first = await client.load({ server: 'PRTG LAB 01', sensorId: 100001, points: 24 });
  await client.load({ server: 'PRTG LAB 01', sensorId: 100001, points: 12 });

  assert.equal(first.metadata.sensorType, 'cpu');
  assert.equal(calls.length, 3, 'metadata is cached per stable sensor lookup');
  assert.ok(calls.every(call => call.options.credentials === 'same-origin'));
  assert.ok(calls.every(call => call.options.cache === 'no-store'));
});

test('client ignores an obsolete response after a newer sensor is selected', async () => {
  let resolveFirst;
  const firstResponse = new Promise(resolve => { resolveFirst = resolve; });
  const secondHistory = payload('cpu', { cpu_usage_pct: channel('CPU usage', '%') });
  const queue = [
    firstResponse,
    Promise.resolve(jsonResponse({
      schema_version: '2.0',
      sensor: { server_id: 'srv-prtg-01', sensor_id: '200002', sensor_type: 'cpu' }
    })),
    Promise.resolve(jsonResponse({
      ...secondHistory,
      sensor: { server_id: 'srv-prtg-01', sensor_id: '200002', sensor_type: 'cpu' }
    }))
  ];
  const client = metrics.createClient({ fetchImpl: () => queue.shift() });
  const obsolete = client.load({ server: 'PRTG LAB 01', sensorId: '100001', points: 2 });
  const current = client.load({ server: 'PRTG LAB 01', sensorId: '200002', points: 2 });
  resolveFirst(jsonResponse({
    schema_version: '2.0',
    sensor: { server_id: 'srv-prtg-01', sensor_id: '100001', sensor_type: 'ping' }
  }));

  await assert.rejects(obsolete, error => metrics.isCancelledRequest(error));
  assert.equal((await current).metadata.sensorId, '200002');
});

test('fallback is limited to unavailable, missing-server and missing-sensor conditions', () => {
  assert.equal(metrics.shouldFallbackToLegacy(
    new metrics.MetricsAPIError('upstream', { status: 502, code: 'upstream_error' })
  ), true);
  assert.equal(metrics.shouldFallbackToLegacy(
    new metrics.MetricsAPIError('missing', { status: 404, code: 'sensor_not_found' })
  ), true);
  assert.equal(metrics.shouldFallbackToLegacy(
    new metrics.MetricsAPIError('bad request', { status: 400, code: 'invalid_request' })
  ), false);
  assert.equal(metrics.shouldFallbackToLegacy(
    new metrics.MetricsAPIError('bug', { status: 500, code: 'internal_error' })
  ), false);
});

test('Ping separates latency from percentage channels', () => {
  const groups = metrics.buildChartGroups(payload('ping', {
    latency_ms: channel('Latency', 'ms', [10, 15]),
    packet_loss_pct: channel('Packet loss', '%', [0, 1]),
    availability_pct: channel('Availability', '%', [100, 99])
  }));
  assert.deepEqual(groups.map(group => group.unit), ['ms', '%']);
  assert.deepEqual(groups[1].datasets.map(item => item.channelKey), [
    'packet_loss_pct', 'availability_pct'
  ]);
});

test('CPU renders the real utilization channel', () => {
  const groups = metrics.buildChartGroups(payload('cpu', {
    cpu_usage_pct: channel('CPU usage', '%', [30, 67])
  }));
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].datasets[0].data.map(point => point.y), [30, 67]);
});

test('Memory keeps percentage and bytes on separate charts', () => {
  const groups = metrics.buildChartGroups(payload('memory', {
    memory_usage_pct: channel('Memory usage', '%', [55, 57]),
    memory_used_bytes: channel('Memory used', 'bytes', [8_000_000_000, 8_500_000_000])
  }));
  assert.deepEqual(groups.map(group => group.unit), ['%', 'bytes']);
});

test('Traffic keeps IN and OUT numeric bps together and utilization separate', () => {
  const groups = metrics.buildChartGroups(payload('traffic', {
    traffic_in_bps: channel('Traffic in', 'bps', [85_000_000, 90_000_000]),
    traffic_out_bps: channel('Traffic out', 'bps', [22_000_000, 25_000_000]),
    utilization_pct: channel('Utilization', '%', [9, 10])
  }));
  assert.deepEqual(groups.map(group => group.unit), ['bps', '%']);
  assert.deepEqual(groups[0].datasets.map(item => item.channelKey), [
    'traffic_in_bps', 'traffic_out_bps'
  ]);
  assert.equal(groups[0].datasets[0].data[0].y, 85_000_000);
});

test('SFP groups compatible units and tolerates partial channels', () => {
  const groups = metrics.buildChartGroups(payload('sfp', {
    rx_power_dbm: channel('RX power', 'dBm', [-3.8, -3.7]),
    tx_power_dbm: channel('TX power', 'dBm', [-2.1, -2.0])
  }));
  assert.equal(groups.length, 1);
  assert.equal(groups[0].unit, 'dBm');
  assert.deepEqual(groups[0].datasets.map(item => item.channelKey), [
    'rx_power_dbm', 'tx_power_dbm'
  ]);
  assert.equal(metrics.diagnosticState(payload('sfp', {
    rx_power_dbm: channel('RX power', 'dBm'),
    tx_power_dbm: channel('TX power', 'dBm')
  })).partial, true);
});

test('issues and unknown channels remain discrete partial diagnostics', () => {
  const result = metrics.diagnosticState(payload('traffic', {
    traffic_in_bps: channel('Traffic in', 'bps'),
    traffic_out_bps: channel('Traffic out', 'bps')
  }, {
    issues: [{ code: 'invalid_value' }],
    unknown_channels: [{ reason: 'unmapped_channel' }]
  }));
  assert.deepEqual(result, {
    issues: 1,
    unknown: 1,
    missing: ['utilization_pct'],
    partial: true
  });
});

test('display formatting scales bytes and rates without mutating source values', () => {
  assert.equal(metrics.formatScaled(85_000_000, 'bps'), '85.00 Mbps');
  assert.equal(metrics.formatScaled(8_500_000_000, 'bytes'), '8.50 GB');
  assert.equal(metrics.formatScaled(-3.8, 'dBm'), '-3.80 dBm');
});

test('payload validation requires numeric values, timezone-aware ordered timestamps', () => {
  const metadata = { serverId: 'srv-prtg-01', sensorId: '100001', sensorType: 'cpu' };
  assert.throws(() => metrics.validateHistoryPayload(payload('cpu', {
    cpu_usage_pct: channel('CPU usage', '%', ['42'])
  }), metadata), /Muestra inválida/);
  const naive = payload('cpu', { cpu_usage_pct: channel('CPU usage', '%') });
  naive.channels.cpu_usage_pct.samples[0].timestamp = '2026-09-23T12:00:00';
  assert.throws(() => metrics.validateHistoryPayload(naive, metadata), /Muestra inválida/);
  const unordered = payload('cpu', { cpu_usage_pct: channel('CPU usage', '%') });
  unordered.channels.cpu_usage_pct.samples.reverse();
  assert.throws(() => metrics.validateHistoryPayload(unordered, metadata), /Serie desordenada/);
});

test('chart configuration uses browser-time axes and unit-aware tooltips', () => {
  const group = metrics.buildChartGroups(payload('traffic', {
    traffic_in_bps: channel('Traffic in', 'bps', [85_000_000])
  }))[0];
  const config = metrics.chartConfig(group);
  assert.equal(config.options.scales.x.type, 'time');
  assert.equal(config.options.scales.y.title.text, 'bps');
  assert.equal(
    config.options.plugins.tooltip.callbacks.label({
      dataset: { label: 'Traffic in' }, parsed: { y: 85_000_000 }
    }),
    'Traffic in: 85.00 Mbps'
  );
});

test('Chart.js cleanup destroys every instance and empties the owner array', () => {
  const destroyed = [];
  const charts = [1, 2, 3].map(value => ({ destroy() { destroyed.push(value); } }));
  metrics.destroyChartInstances(charts);
  assert.deepEqual(destroyed, [1, 2, 3]);
  assert.equal(charts.length, 0);
});
