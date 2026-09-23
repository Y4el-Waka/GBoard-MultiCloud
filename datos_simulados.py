#!/usr/bin/env python3
"""Simulador local de múltiples servidores PRTG para CoreView Cloud Lab.

Expone un subconjunto compatible de la API que usan app.py y monitor.py:
- /<server>/api/table.json?content=devices
- /<server>/api/table.json?content=sensors
- /<server>/api/historicdata.csv
- /<server>/api/pauseobjectfor.htm
- /<server>/api/pause.htm

Además expone superficies sintéticas opt-in para el futuro Metrics Engine:
- /<server>/api/multichannel.json
- /<server>/api/multichannel_history.json

Todos los nombres, IPs y datos son sintéticos.
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import random
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from flask import Flask, Response, abort, jsonify, request

from simulator_multichannel import SyntheticMultichannelSimulator


def _environment_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


DEFAULT_SIMULATOR_SEED = _environment_int("SIMULATOR_SEED", 20260923)
DEFAULT_TICK_SECONDS = _environment_int("SIMULATOR_TICK_SECONDS", 300)
DEFAULT_SCENARIO = os.getenv("SIMULATOR_SCENARIO", "normal") or "normal"

STATUS_NAMES = {
    1: "Unknown",
    3: "Up",
    4: "Warning",
    5: "Down",
    8: "Paused by Dependency",
    9: "Paused by Schedule",
    10: "Unusual",
    12: "Paused Until",
    13: "Down Acknowledged",
    14: "Down Partial",
}

STATUS_WEIGHTS = [
    (3, 0.91),
    (4, 0.035),
    (5, 0.025),
    (13, 0.012),
    (14, 0.008),
    (9, 0.010),
]

SENSOR_KINDS = [
    "Ping",
    "CPU Load",
    "Memory",
    "Traffic In",
    "Traffic Out",
    "HTTP",
    "SNMP Uptime",
    "Packet Loss",
]

DOCUMENTATION_NETS = [
    (192, 0, 2),       # TEST-NET-1
    (198, 51, 100),    # TEST-NET-2
    (203, 0, 113),     # TEST-NET-3
]


@dataclass
class SensorState:
    objid: int
    parentid: int
    device: str
    name: str
    status_raw: int
    lastvalue: str
    message: str = "OK"
    pause_until: datetime | None = None
    pause_indefinite: bool = False
    previous_status: int = 3

    def refresh_pause(self) -> None:
        if self.pause_indefinite:
            return
        if self.pause_until and datetime.now() >= self.pause_until:
            self.pause_until = None
            self.status_raw = self.previous_status or 3
            self.message = "OK"

    def as_prtg(self) -> dict[str, Any]:
        self.refresh_pause()
        return {
            "device": self.device,
            "sensor": self.name,
            "status": STATUS_NAMES.get(self.status_raw, "Unknown"),
            "status_raw": self.status_raw,
            "lastvalue": self.lastvalue,
            "message": self.message,
            "parentid": self.parentid,
            "objid": self.objid,
        }


@dataclass
class SimulatedPRTG:
    slug: str
    name: str
    devices: dict[int, str] = field(default_factory=dict)
    device_names: dict[int, str] = field(default_factory=dict)
    sensors: dict[int, SensorState] = field(default_factory=dict)


class Simulator:
    def __init__(
        self,
        server_count: int,
        sensor_count: int,
        seed: int,
        *,
        scenario: str = "normal",
        tick_seconds: int = 300,
        start_time: datetime | None = None,
    ):
        self.server_count = max(1, server_count)
        self.sensor_count = max(self.server_count, sensor_count)
        self.seed = seed
        self.random = random.Random(seed)
        self.lock = threading.RLock()
        self.servers: dict[str, SimulatedPRTG] = {}
        self._build()
        # Se construye después del dataset legacy y usa un PRNG derivado propio.
        # Por eso no consume ni altera la secuencia aleatoria legacy.
        self.multichannel = SyntheticMultichannelSimulator(
            self.servers,
            seed=seed,
            scenario=scenario,
            tick_seconds=tick_seconds,
            start_time=start_time,
        )

    def _weighted_status(self) -> int:
        pick = self.random.random()
        acc = 0.0
        for status, weight in STATUS_WEIGHTS:
            acc += weight
            if pick <= acc:
                return status
        return 3

    @staticmethod
    def _safe_ip(index: int) -> str:
        # Repite direcciones entre sensores porque la IP pertenece al dispositivo,
        # no al sensor. Estos rangos están reservados para documentación.
        net = DOCUMENTATION_NETS[(index // 254) % len(DOCUMENTATION_NETS)]
        host = (index % 254) + 1
        return f"{net[0]}.{net[1]}.{net[2]}.{host}"

    def _lastvalue(self, kind: str, status: int) -> str:
        if status in {5, 13, 14}:
            return "Request timed out" if kind == "Ping" else "0"
        if kind == "Ping":
            return f"{self.random.randint(2, 75)} ms"
        if kind == "CPU Load":
            return f"{self.random.randint(8, 92)} %"
        if kind == "Memory":
            return f"{self.random.randint(20, 88)} %"
        if "Traffic" in kind:
            return f"{self.random.uniform(1, 950):.1f} Mbit/s"
        if kind == "HTTP":
            return f"{self.random.randint(80, 750)} ms"
        if kind == "Packet Loss":
            return f"{self.random.uniform(0, 4):.1f} %"
        return f"{self.random.randint(1, 180)} d"

    def _build(self) -> None:
        per_server = [self.sensor_count // self.server_count] * self.server_count
        for i in range(self.sensor_count % self.server_count):
            per_server[i] += 1

        next_device_id = 10_000
        next_sensor_id = 100_000
        global_device_idx = 0

        for server_idx in range(1, self.server_count + 1):
            slug = f"prtg-{server_idx:02d}"
            server = SimulatedPRTG(slug=slug, name=f"PRTG LAB {server_idx:02d}")
            target_sensors = per_server[server_idx - 1]
            device_count = max(5, math.ceil(target_sensors / 5))

            for device_idx in range(1, device_count + 1):
                device_id = next_device_id
                next_device_id += 1
                global_device_idx += 1
                device_name = f"SITE-{server_idx:02d}-{device_idx:03d}"
                server.devices[device_id] = self._safe_ip(global_device_idx)
                server.device_names[device_id] = device_name

            device_ids = list(server.devices)
            for sensor_idx in range(target_sensors):
                parent_id = device_ids[sensor_idx % len(device_ids)]
                kind = SENSOR_KINDS[sensor_idx % len(SENSOR_KINDS)]

                # Aproximadamente 1 de cada 40 sensores es MF para probar el SLA.
                if sensor_idx % 40 == 0:
                    kind = f"MF-{kind}"

                status = self._weighted_status()
                if kind.startswith("MF-") and sensor_idx % 80 == 0:
                    status = 5

                sensor = SensorState(
                    objid=next_sensor_id,
                    parentid=parent_id,
                    device=server.device_names[parent_id],
                    name=kind,
                    status_raw=status,
                    lastvalue=self._lastvalue(kind.removeprefix("MF-"), status),
                    message="Synthetic monitoring data",
                    previous_status=3,
                )
                server.sensors[next_sensor_id] = sensor
                next_sensor_id += 1

            self.servers[slug] = server

    def server(self, slug: str) -> SimulatedPRTG:
        server = self.servers.get(slug)
        if not server:
            abort(404)
        return server

    def pause_for(self, slug: str, sensor_id: int, minutes: int, message: str) -> None:
        with self.lock:
            sensor = self.server(slug).sensors.get(sensor_id)
            if not sensor:
                abort(404)
            sensor.refresh_pause()
            if sensor.status_raw not in {9, 12}:
                sensor.previous_status = sensor.status_raw
            sensor.pause_indefinite = False
            sensor.pause_until = datetime.now() + timedelta(minutes=max(1, minutes))
            sensor.status_raw = 12
            sensor.message = f"Paused until {sensor.pause_until.strftime('%Y-%m-%d %H:%M:%S')} · {message}".strip()

    def set_pause(self, slug: str, sensor_id: int, paused: bool, message: str = "") -> None:
        with self.lock:
            sensor = self.server(slug).sensors.get(sensor_id)
            if not sensor:
                abort(404)
            sensor.refresh_pause()
            if paused:
                if sensor.status_raw not in {9, 12}:
                    sensor.previous_status = sensor.status_raw
                sensor.pause_until = None
                sensor.pause_indefinite = True
                sensor.status_raw = 9
                sensor.message = message or "Paused by synthetic PRTG"
            else:
                sensor.pause_until = None
                sensor.pause_indefinite = False
                sensor.status_raw = sensor.previous_status if sensor.previous_status not in {9, 12} else 3
                sensor.message = "OK"

    def tick(self, steps: int = 1) -> datetime:
        return self.multichannel.tick(steps)

    def set_scenario(self, scenario: str) -> None:
        self.multichannel.set_scenario(scenario)

    def multichannel_sensor(
        self,
        slug: str,
        sensor_id: int,
        *,
        scenario: str | None = None,
        fault: str | None = None,
    ) -> dict[str, Any]:
        self.server(slug)
        return self.multichannel.sensor_source(
            slug,
            sensor_id,
            scenario=scenario,
            fault=fault,
        )

    def multichannel_history(
        self,
        slug: str,
        sensor_id: int,
        *,
        points: int = 12,
        scenario: str | None = None,
        fault: str | None = None,
    ) -> dict[str, Any]:
        self.server(slug)
        return self.multichannel.historic_source(
            slug,
            sensor_id,
            points=points,
            scenario=scenario,
            fault=fault,
        )


parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--servers", type=int, default=13)
parser.add_argument("--sensors", type=int, default=1000)
parser.add_argument("--seed", type=int, default=DEFAULT_SIMULATOR_SEED)
parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
parser.add_argument("--tick-seconds", type=int, default=DEFAULT_TICK_SECONDS)
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=9090)
_known_args, _unknown = parser.parse_known_args()

sim = Simulator(
    _known_args.servers,
    _known_args.sensors,
    _known_args.seed,
    scenario=_known_args.scenario,
    tick_seconds=_known_args.tick_seconds,
)
app = Flask(__name__)


@app.get("/healthz")
def healthz():
    return jsonify({
        "ok": True,
        "synthetic": True,
        "servers": len(sim.servers),
        "sensors": sum(len(s.sensors) for s in sim.servers.values()),
        "seed": sim.seed,
    })


@app.get("/<slug>/api/table.json")
def table_json(slug: str):
    server = sim.server(slug)
    content = request.args.get("content", "sensors").lower()

    if content == "devices":
        return jsonify({
            "devices": [
                {"objid": objid, "host": host}
                for objid, host in server.devices.items()
            ]
        })

    if content == "sensors":
        with sim.lock:
            return jsonify({"sensors": [sensor.as_prtg() for sensor in server.sensors.values()]})

    return jsonify({content: []})


@app.get("/<slug>/api/multichannel.json")
def multichannel_json(slug: str):
    sensor_id = request.args.get("id", type=int)
    scenario = request.args.get("scenario")
    fault = request.args.get("fault")
    advance = request.args.get("advance", default=0, type=int)

    try:
        with sim.lock:
            sim.server(slug)
            if advance > 0:
                sim.tick(advance)
            if sensor_id is not None:
                payload = sim.multichannel_sensor(
                    slug,
                    sensor_id,
                    scenario=scenario,
                    fault=fault,
                )
            else:
                payload = sim.multichannel.server_source(
                    slug,
                    scenario=scenario,
                    fault=fault,
                )
    except KeyError:
        abort(404)
    except ValueError as exc:
        abort(400, description=str(exc))
    return jsonify(payload)


@app.get("/<slug>/api/multichannel_history.json")
def multichannel_history_json(slug: str):
    sensor_id = request.args.get("id", type=int)
    points = request.args.get("points", default=12, type=int)
    if sensor_id is None:
        abort(400)

    try:
        with sim.lock:
            payload = sim.multichannel_history(
                slug,
                sensor_id,
                points=points,
                scenario=request.args.get("scenario"),
                fault=request.args.get("fault"),
            )
    except KeyError:
        abort(404)
    except ValueError as exc:
        abort(400, description=str(exc))
    return jsonify(payload)


@app.get("/<slug>/api/pauseobjectfor.htm")
def pause_for(slug: str):
    sensor_id = request.args.get("id", type=int)
    duration = request.args.get("duration", default=60, type=int)
    if sensor_id is None:
        abort(400)
    sim.pause_for(slug, sensor_id, duration, request.args.get("pausemsg", ""))
    return Response("OK\n", mimetype="text/plain")


@app.get("/<slug>/api/pause.htm")
def pause(slug: str):
    sensor_id = request.args.get("id", type=int)
    action = request.args.get("action", default=0, type=int)
    if sensor_id is None:
        abort(400)
    sim.set_pause(slug, sensor_id, paused=(action == 0), message=request.args.get("pausemsg", ""))
    return Response("OK\n", mimetype="text/plain")


@app.get("/<slug>/api/historicdata.csv")
def historic(slug: str):
    server = sim.server(slug)
    sensor_id = request.args.get("id", type=int)
    if sensor_id is None or sensor_id not in server.sensors:
        abort(404)

    avg_seconds = max(60, request.args.get("avg", default=300, type=int))
    try:
        start = datetime.strptime(request.args.get("sdate", ""), "%Y-%m-%d-%H-%M-%S")
        end = datetime.strptime(request.args.get("edate", ""), "%Y-%m-%d-%H-%M-%S")
    except ValueError:
        end = datetime.now()
        start = end - timedelta(hours=2)

    rows: list[list[str]] = [[
        "Date Time",
        "Ping Time",
        "Minimum",
        "Maximum",
        "Packet Loss",
        "Downtime",
        "Coverage",
    ]]

    local_random = random.Random(sim.seed + sensor_id)
    cursor = start
    base_ping = local_random.randint(5, 40)
    while cursor <= end:
        ping = max(1.0, base_ping + local_random.uniform(-4, 9))
        loss = max(0.0, min(100.0, local_random.uniform(0, 2.5)))
        downtime = 100.0 if loss >= 99 else 0.0
        coverage = 100.0
        rows.append([
            cursor.strftime("%Y-%m-%d %H:%M:%S"),
            f"{ping:.2f} ms",
            f"{max(0.5, ping - local_random.uniform(1, 4)):.2f} ms",
            f"{ping + local_random.uniform(1, 8):.2f} ms",
            f"{loss:.2f} %",
            f"{downtime:.2f} %",
            f"{coverage:.2f} %",
        ])
        cursor += timedelta(seconds=avg_seconds)

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerows(rows)
    return Response(buffer.getvalue(), mimetype="text/csv")


def main() -> None:
    args = argparse.ArgumentParser(description="Simulador PRTG local para CoreView Cloud Lab")
    args.add_argument("--servers", type=int, default=13, help="Cantidad de PRTG sintéticos")
    args.add_argument("--sensors", type=int, default=1000, help="Cantidad total de sensores")
    args.add_argument("--seed", type=int, default=DEFAULT_SIMULATOR_SEED, help="Semilla reproducible")
    args.add_argument("--scenario", default=DEFAULT_SCENARIO, help="Escenario multicanal")
    args.add_argument("--tick-seconds", type=int, default=DEFAULT_TICK_SECONDS, help="Segundos por tick lógico")
    args.add_argument("--host", default="127.0.0.1")
    args.add_argument("--port", type=int, default=9090)
    parsed = args.parse_args()

    # Si los argumentos difieren de los usados al importar, reconstruimos el dataset.
    global sim
    sim = Simulator(
        parsed.servers,
        parsed.sensors,
        parsed.seed,
        scenario=parsed.scenario,
        tick_seconds=parsed.tick_seconds,
    )

    print(f"Synthetic PRTG simulator: http://{parsed.host}:{parsed.port}")
    print(f"Servers: {len(sim.servers)} | Sensors: {sum(len(s.sensors) for s in sim.servers.values())}")
    print("All data is synthetic. No production information is used.")
    app.run(host=parsed.host, port=parsed.port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
