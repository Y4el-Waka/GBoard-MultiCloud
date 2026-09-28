#!/usr/bin/env node
import fs from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium, request } from 'playwright';

const baseURL = process.env.COREVIEW_BROWSER_BASE_URL || 'http://coreview-offline-app:5067';
const simulatorURL = process.env.COREVIEW_BROWSER_SIMULATOR_URL || 'http://coreview-offline-app:9090';
const username = process.env.COREVIEW_LAB_ADMIN_USER || 'lab-admin';
const password = process.env.COREVIEW_LAB_ADMIN_PASSWORD || '';
const outputDir = process.env.COREVIEW_BROWSER_ARTIFACTS || '/artifacts';
const allowedHost = new URL(baseURL).hostname;

if (!password) throw new Error('COREVIEW_LAB_ADMIN_PASSWORD is required');
if (new URL(simulatorURL).hostname !== allowedHost) {
  throw new Error('Application and simulator must share the isolated laboratory host');
}

const expectedChannels = {
  ping: ['latency_ms', 'packet_loss_pct', 'availability_pct'],
  cpu: ['cpu_usage_pct'],
  memory: ['memory_usage_pct', 'memory_used_bytes'],
  traffic: ['traffic_in_bps', 'traffic_out_bps', 'utilization_pct'],
  sfp: ['rx_power_dbm', 'tx_power_dbm', 'temperature_c', 'voltage_v', 'bias_current_ma']
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function screenshot(page, name) {
  await page.screenshot({ path: path.join(outputDir, `${name}.png`), animations: 'disabled' });
}

async function catalogSensors() {
  const client = await request.newContext({ baseURL: simulatorURL });
  try {
    const response = await client.get('/prtg-01/api/multichannel.json');
    assert(response.ok(), `Synthetic catalog returned HTTP ${response.status()}`);
    const payload = await response.json();
    assert(payload.server_name === 'PRTG LAB 01', 'Unexpected synthetic server identity');
    const byFamily = new Map();
    for (const sensor of payload.sensors || []) {
      const family = String(sensor.sensor_type || '');
      if (!expectedChannels[family]) continue;
      const current = byFamily.get(family);
      if (!current || (sensor.channels || []).length > (current.channels || []).length) {
        byFamily.set(family, sensor);
      }
    }
    for (const family of Object.keys(expectedChannels)) {
      assert(byFamily.has(family), `No ${family} sensor in synthetic catalog`);
    }
    const sfpSensors = (payload.sensors || []).filter(sensor => sensor.sensor_type === 'sfp');
    const partialSfp = sfpSensors.sort(
      (left, right) => (left.channels || []).length - (right.channels || []).length
    )[0];
    return { serverName: payload.server_name, byFamily, partialSfp };
  } finally {
    await client.dispose();
  }
}

async function historyButton(page, serverName, sensorId) {
  const button = page.locator(
    `.history-btn[data-group="${serverName}"][data-id="${sensorId}"]`
  );
  await button.waitFor({ state: 'visible', timeout: 30_000 });
  return button;
}

async function openHistory(page, serverName, sensor, family, artifactName) {
  const sensorId = String(sensor.objid);
  const responsePromise = page.waitForResponse(
    response => response.url().includes('/api/v2/metrics/history?') && response.status() === 200,
    { timeout: 20_000 }
  );
  await (await historyButton(page, serverName, sensorId)).click();
  const response = await responsePromise;
  const payload = await response.json();
  assert(payload.sensor.sensor_type === family, `${family} metadata/history identity mismatch`);
  const keys = Object.keys(payload.channels || {});
  for (const channel of expectedChannels[family]) {
    assert(keys.includes(channel), `${family} is missing ${channel}`);
  }
  await page.locator('#history-state').filter({ hasText: /cargado|muestran/i }).waitFor();
  assert(await page.locator('.metrics-history-chart-panel canvas').count() > 0, `${family} chart missing`);
  await screenshot(page, artifactName);
  await page.locator('#history-close').click();
  await page.locator('#history-modal').waitFor({ state: 'hidden' });
  return payload;
}

await fs.mkdir(outputDir, { recursive: true });
for (const entry of await fs.readdir(outputDir)) {
  if (/^(?:\d{2}-.+|failure)\.png$/.test(entry) || entry === 'report.json') {
    await fs.rm(path.join(outputDir, entry));
  }
}
const externalRequests = [];
const failedRequests = [];
const consoleErrors = [];
const cspErrors = [];
let expectedFallbackActive = false;
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  baseURL,
  viewport: { width: 1440, height: 1000 },
  colorScheme: 'dark',
  locale: 'es-MX',
  timezoneId: 'America/Mexico_City'
});
const page = await context.newPage();

await page.route('**/*', async route => {
  const url = new URL(route.request().url());
  if (['data:', 'blob:'].includes(url.protocol) || url.hostname === allowedHost) {
    await route.continue();
    return;
  }
  externalRequests.push(route.request().url());
  await route.abort('blockedbyclient');
});
page.on('requestfailed', request => {
  if (new URL(request.url()).hostname === allowedHost) {
    failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || 'failed'}`);
  }
});
page.on('console', message => {
  if (message.type() !== 'error') return;
  if (expectedFallbackActive && /Failed to load resource/i.test(message.text())) return;
  consoleErrors.push(message.text());
  if (/content security policy|refused to/i.test(message.text())) cspErrors.push(message.text());
});
page.on('pageerror', error => consoleErrors.push(error.message));
page.on('dialog', dialog => dialog.accept());

const catalog = await catalogSensors();
const report = {
  schema_version: 1,
  synthetic_only: true,
  external_network_blocked: true,
  base_url: baseURL,
  browser: 'chromium',
  families: {},
  checks: {}
};

try {
  const loginResponse = await page.goto('/', { waitUntil: 'networkidle' });
  assert(loginResponse?.ok(), 'Login page unavailable');
  const csp = loginResponse.headers()['content-security-policy'] || '';
  assert(csp && !csp.includes("'unsafe-inline'") && !csp.includes("'unsafe-eval'"), 'CSP is not hardened');
  assert(!/https?:\/\//.test(csp), 'CSP contains an external origin');
  await screenshot(page, '01-login-offline');

  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await Promise.all([
    page.waitForURL('**/dashboard', { timeout: 20_000 }),
    page.locator('button[type="submit"]').click()
  ]);
  await page.locator('#total-sensors').filter({ hasText: '1000' }).waitFor({ timeout: 30_000 });
  await page.waitForLoadState('networkidle');

  const runtime = await page.evaluate(() => ({
    chart: globalThis.Chart?.version || null,
    jquery: globalThis.jQuery?.fn?.jquery || null,
    toastr: typeof globalThis.toastr === 'object',
    metricsClient: typeof globalThis.CoreViewMetricsHistory === 'object',
    bodyFont: getComputedStyle(document.body).fontFamily,
    iconFont: getComputedStyle(document.querySelector('.fa-solid, .fas'), '::before').fontFamily,
    metricsEnabled: document.body.dataset.metricsUiEnabled
  }));
  assert(runtime.chart === '4.5.1', `Unexpected Chart.js ${runtime.chart}`);
  assert(runtime.jquery === '3.7.1', `Unexpected jQuery ${runtime.jquery}`);
  assert(runtime.toastr && runtime.metricsClient, 'Local frontend libraries did not initialize');
  assert(/Inter/i.test(runtime.bodyFont), 'Local Inter font is not active');
  assert(/Font Awesome/i.test(runtime.iconFont), 'Local Font Awesome font is not active');
  assert(runtime.metricsEnabled === 'true', 'Metrics UI feature flag is disabled');
  report.checks.runtime = runtime;
  await screenshot(page, '02-dashboard-offline');

  await page.locator('#tab-logs').click();
  await page.locator('#logs-view').waitFor({ state: 'visible' });
  await screenshot(page, '03-logs');
  await page.locator('#tab-trends').click();
  await page.locator('#trends-view').waitFor({ state: 'visible' });
  assert(await page.locator('#metricsChart').isVisible(), 'Trends chart is not visible');
  await screenshot(page, '04-trends');
  await page.locator('#tab-sensors').click();

  let index = 5;
  for (const family of Object.keys(expectedChannels)) {
    const sensor = catalog.byFamily.get(family);
    const payload = await openHistory(
      page,
      catalog.serverName,
      sensor,
      family,
      `${String(index).padStart(2, '0')}-history-${family}`
    );
    report.families[family] = Object.keys(payload.channels).sort();
    index += 1;
  }

  const partial = catalog.partialSfp;
  const partialId = String(partial.objid);
  const partialResponse = page.waitForResponse(
    response => response.url().includes('/api/v2/metrics/history?') && response.status() === 200
  );
  await (await historyButton(page, catalog.serverName, partialId)).click();
  const partialPayload = await (await partialResponse).json();
  const partialKeys = Object.keys(partialPayload.channels || {});
  assert(partialKeys.length > 0 && partialKeys.length < expectedChannels.sfp.length, 'SFP partial contract not exercised');
  await page.locator('#history-mode').filter({ hasText: /parciales/i }).waitFor();
  await screenshot(page, '10-history-sfp-partial');
  await page.locator('#history-close').click();
  report.checks.partial_sfp = partialKeys.sort();

  const ping = catalog.byFamily.get('ping');
  expectedFallbackActive = true;
  await page.route('**/api/v2/metrics/history?*', async route => {
    await route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ code: 'upstream_error', error: 'Synthetic provider unavailable' })
    });
  });
  await (await historyButton(page, catalog.serverName, String(ping.objid))).click();
  await page.locator('#history-mode').filter({ hasText: /Modo legacy/i }).waitFor({ timeout: 20_000 });
  await page.locator('#history-state').filter({ hasText: /legacy cargado/i }).waitFor();
  await screenshot(page, '11-legacy-fallback');
  await page.locator('#history-close').click();
  await page.unroute('**/api/v2/metrics/history?*');
  expectedFallbackActive = false;
  report.checks.legacy_fallback = true;

  const pingButton = await historyButton(page, catalog.serverName, String(ping.objid));
  const card = pingButton.locator('xpath=ancestor::article[1]');
  const pause = card.locator('.sensor-pause-btn[data-action="pause"]');
  await pause.click();
  await page.locator('#sensor-pause-modal').waitFor({ state: 'visible' });
  const pauseResponse = page.waitForResponse(
    response => response.url().includes('/api/sensors/pause') && response.status() === 200
  );
  await page.locator('.pause-quick-option[data-minutes="60"]').click();
  await pauseResponse;
  await page.locator('#sensor-pause-modal').waitFor({ state: 'hidden' });
  const resumeResponse = page.waitForResponse(
    response => response.url().includes('/api/sensors/resume') && response.status() === 200
  );
  await (await historyButton(page, catalog.serverName, String(ping.objid))).click();
  await page.locator('#history-resume-btn').click();
  await resumeResponse;
  await page.locator('#history-close').click();
  report.checks.pause_resume = true;

  await page.locator('#search').fill(String(ping.sensor_name || ping.name || ping.objid));
  assert(await page.locator('.sensor-card').count() > 0, 'Search produced no sensor cards');
  await page.locator('#search').fill('');
  report.checks.filters = true;

  assert(externalRequests.length === 0, `External requests attempted: ${externalRequests.join(', ')}`);
  assert(failedRequests.length === 0, `Local requests failed: ${failedRequests.join(', ')}`);
  assert(cspErrors.length === 0, `CSP errors: ${cspErrors.join(' | ')}`);
  assert(consoleErrors.length === 0, `Browser console errors: ${consoleErrors.join(' | ')}`);
  report.external_requests = externalRequests;
  report.local_request_failures = failedRequests;
  report.console_errors = consoleErrors;
  report.ok = true;
  await fs.writeFile(path.join(outputDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  report.ok = false;
  report.error = error instanceof Error ? error.message : String(error);
  report.external_requests = externalRequests;
  report.local_request_failures = failedRequests;
  report.console_errors = consoleErrors;
  await fs.writeFile(path.join(outputDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  await screenshot(page, 'failure').catch(() => {});
  throw error;
} finally {
  await context.close();
  await browser.close();
}
