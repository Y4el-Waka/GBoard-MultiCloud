# Laboratory Metrics Provider Smoke Test

This procedure runs the complete local CoreView laboratory path. It uses only
loopback connections and synthetic data.

## 1. Prepare explicit configuration

```bash
cp .env.example .env
cp servers.example.json servers.json
```

Set the following in the untracked `.env`:

```dotenv
COREVIEW_METRICS_PROVIDER=synthetic
APP_SECRET_KEY=replace-with-a-local-development-secret
```

`servers.json` should retain the synthetic loopback URLs from
`servers.example.json`, such as
`http://127.0.0.1:9090/prtg-01`. The provider rejects non-loopback hosts,
embedded URL credentials, redirects, query strings, and fragments.

## 2. Start the simulator

Terminal 1:

```bash
.venv/bin/python datos_simulados.py \
  --host 127.0.0.1 \
  --port 9090 \
  --servers 13 \
  --sensors 1000
```

Verify its synthetic topology:

```bash
curl --fail --silent http://127.0.0.1:9090/healthz
```

The response should report 13 servers and 1000 sensors.

## 3. Start the collector

Terminal 2:

```bash
.venv/bin/python monitor.py
```

Wait for `estado_sensores_prtg.json` to report
`servers_configured: 13` and `servers_with_error: 0`.

## 4. Create a local user and start CoreView

Terminal 3:

```bash
.venv/bin/flask --app app create-user lab-admin '<local-password>' --rol admon
.venv/bin/flask --app app run --host 127.0.0.1 --port 5067
```

The startup log should include:

```text
Metrics API v2 laboratory provider enabled: synthetic
```

Using the Flask CLI rather than `python app.py` in this smoke procedure keeps
the web server restricted to localhost.

## 5. Discover valid synthetic sensor IDs

Do not guess IDs. Query the simulator source and print the first ID for each
family:

```bash
curl --fail --silent \
  http://127.0.0.1:9090/prtg-01/api/multichannel.json \
  | .venv/bin/python -c '
import json, sys
payload = json.load(sys.stdin)
found = {}
for sensor in payload["sensors"]:
    found.setdefault(sensor["sensor_type"], sensor["objid"])
for family in ("ping", "cpu", "memory", "traffic", "sfp"):
    print(f"{family}: {found[family]}")
'
```

Use the printed values as `<PING_ID>`, `<CPU_ID>`, `<TRAFFIC_ID>`, and
`<SFP_ID>` below.

## 6. Authenticate

```bash
curl --fail --silent \
  --cookie-jar /tmp/coreview-lab.cookies \
  --data-urlencode 'username=lab-admin' \
  --data-urlencode 'password=<local-password>' \
  http://127.0.0.1:5067/
```

The cookie file is runtime-only. Remove it after the smoke test.

## 7. Query normalized metrics

Ping:

```bash
curl --fail --silent --get \
  --cookie /tmp/coreview-lab.cookies \
  --data-urlencode 'server=PRTG LAB 01' \
  --data-urlencode 'sensor_id=<PING_ID>' \
  --data-urlencode 'sensor_type=ping' \
  --data-urlencode 'points=6' \
  http://127.0.0.1:5067/api/v2/metrics/history
```

CPU:

```bash
curl --fail --silent --get \
  --cookie /tmp/coreview-lab.cookies \
  --data-urlencode 'server=PRTG LAB 01' \
  --data-urlencode 'sensor_id=<CPU_ID>' \
  --data-urlencode 'sensor_type=cpu' \
  --data-urlencode 'points=6' \
  http://127.0.0.1:5067/api/v2/metrics/history
```

Traffic:

```bash
curl --fail --silent --get \
  --cookie /tmp/coreview-lab.cookies \
  --data-urlencode 'server=PRTG LAB 01' \
  --data-urlencode 'sensor_id=<TRAFFIC_ID>' \
  --data-urlencode 'sensor_type=traffic' \
  --data-urlencode 'points=6' \
  http://127.0.0.1:5067/api/v2/metrics/history
```

Confirm that `traffic_in_bps` and `traffic_out_bps` are separate numeric
series with unit `bps`.

SFP:

```bash
curl --fail --silent --get \
  --cookie /tmp/coreview-lab.cookies \
  --data-urlencode 'server=PRTG LAB 01' \
  --data-urlencode 'sensor_id=<SFP_ID>' \
  --data-urlencode 'sensor_type=sfp' \
  --data-urlencode 'points=6' \
  http://127.0.0.1:5067/api/v2/metrics/history
```

Different synthetic SFP sensors intentionally expose different channel
subsets.

## 8. Range-mode example

At its default logical start, the simulator's current time is
`2026-09-23T12:00:00Z`. Without explicit advancement, this request returns
the real 11:50, 11:55, and 12:00 ticks:

```bash
curl --fail --silent --get \
  --cookie /tmp/coreview-lab.cookies \
  --data-urlencode 'server=PRTG LAB 01' \
  --data-urlencode 'sensor_id=<CPU_ID>' \
  --data-urlencode 'sensor_type=cpu' \
  --data-urlencode 'start=2026-09-23T11:50:00Z' \
  --data-urlencode 'end=2026-09-23T12:00:00Z' \
  http://127.0.0.1:5067/api/v2/metrics/history
```

If the logical clock has been advanced, obtain the current source timestamp
from `/<server>/api/multichannel.json?id=<sensor_id>` and choose aware UTC
boundaries around actual five-minute ticks.

## 9. Stop and clean up

Stop CoreView, collector, and simulator with `Ctrl-C`, then remove the
temporary cookie:

```bash
rm /tmp/coreview-lab.cookies
```

Do not commit `.env`, `servers.json`, runtime databases, generated snapshot,
logs, or cookie files.
