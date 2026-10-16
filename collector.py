"""
Real-device telemetry collector (psutil).

Samples CPU, memory and battery from THIS machine and appends rows to a CSV in the
same schema as the synthetic fleet, so both can be ingested into the same table.

Usage:
    python collector.py --interval 30 --samples 120 --out data/local_telemetry.csv
    (run it for a few hours/days; leave --samples 0 to run until Ctrl+C)

Note: psutil.sensors_battery() returns None on desktops / machines without a battery.
In that case battery_pct is left empty and only CPU/memory are real.
"""
import argparse
import csv
import os
import platform
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

FIELDS = ["timestamp", "device_id", "os_build", "hardware_model", "region",
          "cpu_pct", "mem_pct", "battery_pct"]


def sample(device_id, os_build, model, region):
    batt = psutil.sensors_battery()
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "device_id": device_id,
        "os_build": os_build,
        "hardware_model": model,
        "region": region,
        "cpu_pct": psutil.cpu_percent(interval=1),
        "mem_pct": psutil.virtual_memory().percent,
        "battery_pct": round(batt.percent, 2) if batt else "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="seconds between samples")
    ap.add_argument("--samples", type=int, default=0, help="0 = run until Ctrl+C")
    ap.add_argument("--region", default="IN-West")
    ap.add_argument("--out", default="data/local_telemetry.csv")
    args = ap.parse_args()

    device_id = f"local-{socket.gethostname()}"
    os_build = f"{platform.system()}-{platform.release()}"
    model = "Local-Machine"

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()

    n = 0
    with out.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        try:
            while args.samples == 0 or n < args.samples:
                row = sample(device_id, os_build, model, args.region)
                w.writerow(row)
                f.flush()
                n += 1
                print(f"[{n}] cpu={row['cpu_pct']}% mem={row['mem_pct']}% battery={row['battery_pct'] or 'n/a'}")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
