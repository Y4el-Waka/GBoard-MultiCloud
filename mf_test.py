#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
JSON_PATH = BASE_DIR / "estado_sensores_prtg.json"

SENSOR_ID = "MF-TEST-001"


def load_data() -> dict:
    if not JSON_PATH.exists():
        raise SystemExit(
            f"No existe {JSON_PATH.name} en {BASE_DIR}\n"
            "Coloca este archivo dentro de la carpeta principal de GBOARD."
        )

    try:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"El JSON no es válido: {exc}") from exc


def save_data(data: dict) -> None:
    JSON_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_mf(minutes_remaining: int) -> None:
    if minutes_remaining < 1:
        raise SystemExit("--minutes debe ser mayor o igual a 1")

    data = load_data()
    now = datetime.now()

    # SLA total: 6 horas. Se simula que ya transcurrió casi todo
    # para que queden únicamente los minutos solicitados.
    deadline = now + timedelta(minutes=minutes_remaining)
    started = deadline - timedelta(hours=6)

    sensor = {
        "device": "CLIENTE PRUEBA MF",
        "sensor": "MF ENLACE TEST",
        "status": "Down",
        "status_code": 5,
        "lastvalue": "Request timed out",
        "grupo": "PRTG TEST",
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "message": "MF simulado para pruebas",
        "ip_address": "192.0.2.50",
        "sensor_id": SENSOR_ID,
        "parent_id": "999999",
        "is_mf": True,
        "mf_alert_active": True,
        "mf_alert_started_at": started.isoformat(timespec="seconds"),
        "mf_sla_deadline": deadline.isoformat(timespec="seconds"),
        "mf_sla_hours": 6,
        "mf_sla_breached": False,
    }

    data.setdefault("sensores", [])

    # Si ya existe, lo reemplaza para no duplicarlo.
    data["sensores"] = [
        item
        for item in data["sensores"]
        if str(item.get("sensor_id")) != SENSOR_ID
    ]

    data["sensores"].append(sensor)
    data["ultima_actualizacion"] = now.strftime("%Y-%m-%d %H:%M:%S")

    save_data(data)

    print()
    print("MF DE PRUEBA AGREGADO")
    print("---------------------")
    print(f"Sensor : {sensor['sensor']}")
    print(f"ID     : {SENSOR_ID}")
    print(f"Inicio : {started.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Vence  : {deadline.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Restan : {minutes_remaining} minuto(s)")
    print()
    print("Recarga el dashboard con Ctrl + Shift + R.")
    print("IMPORTANTE: si monitor.py está corriendo, puede sobrescribir el JSON.")


def remove_mf() -> None:
    data = load_data()
    sensors = data.get("sensores", [])

    before = len(sensors)

    data["sensores"] = [
        item
        for item in sensors
        if str(item.get("sensor_id")) != SENSOR_ID
    ]

    removed = before - len(data["sensores"])
    data["ultima_actualizacion"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    save_data(data)

    if removed:
        print(f"MF de prueba eliminado: {SENSOR_ID}")
    else:
        print("No había ningún MF de prueba para eliminar.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Agrega o elimina un sensor MF simulado en estado_sensores_prtg.json."
    )

    parser.add_argument(
        "--minutes",
        type=int,
        default=5,
        help="Minutos restantes antes de vencer el SLA. Default: 5.",
    )

    parser.add_argument(
        "--remove",
        action="store_true",
        help="Elimina el MF de prueba.",
    )

    args = parser.parse_args()

    if args.remove:
        remove_mf()
    else:
        add_mf(args.minutes)


if __name__ == "__main__":
    main()