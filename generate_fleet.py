"""
Synthetic device fleet telemetry generator.

Simulates a fleet of devices reporting CPU, memory and battery metrics, with
deliberately injected regressions so detection can be validated against ground truth.

Scenario
--------
- Fleet of N devices across 3 hardware models, 3 regions.
- All devices start on build-1. At ROLLOUT_DAY, a random half of devices
  move to build-2.
- Injected regressions (only on build-2 devices after rollout):
    1. Model-B : slow memory leak (mem_pct creeps up over time, reset by daily restart
                 on a minority of devices to keep data realistic)
    2. Model-C : battery drain rate roughly doubles
- Optional data-quality faults: dropped heartbeats, duplicate rows, out-of-range values.

Usage
-----
    python generate_fleet.py --devices 40 --days 7 --seed 42 --out data/fleet_telemetry.csv
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MODELS = ["Model-A", "Model-B", "Model-C"]
REGIONS = ["IN-West", "EU-North", "US-East"]
INTERVAL_MIN = 5

# Baseline behaviour per hardware model
MODEL_PROFILE = {
    "Model-A": {"cpu": 22, "mem": 48, "drain_per_hr": 6.0},
    "Model-B": {"cpu": 28, "mem": 55, "drain_per_hr": 7.0},
    "Model-C": {"cpu": 18, "mem": 42, "drain_per_hr": 5.0},
}

# Regression parameters
LEAK_PCT_PER_HR = 0.35        # Model-B / build-2 memory growth per hour after rollout
LEAK_CAP = 95.0
DRAIN_MULTIPLIER = 2.0        # Model-C / build-2 battery drain multiplier


def simulate_device(rng, device_id, model, region, timestamps, rollout_ts, on_new_build):
    n = len(timestamps)
    prof = MODEL_PROFILE[model]
    hours = np.arange(n) * INTERVAL_MIN / 60.0
    hour_of_day = timestamps.hour.values + timestamps.minute.values / 60.0

    # Daily usage pattern: busier during the day
    daily = 1 + 0.35 * np.sin((hour_of_day - 9) / 24 * 2 * np.pi)

    # CPU
    cpu = prof["cpu"] * daily + rng.normal(0, 4, n)
    cpu += rng.binomial(1, 0.01, n) * rng.uniform(20, 40, n)  # occasional benign spikes

    # Memory: slow baseline wander
    mem = prof["mem"] + 3 * np.sin(hours / 12) + rng.normal(0, 1.5, n)

    after = np.asarray(timestamps >= rollout_ts) if on_new_build else np.zeros(n, dtype=bool)

    # Regression 1: memory leak on Model-B + build-2
    if model == "Model-B" and on_new_build:
        hrs_since = np.where(after, (timestamps - rollout_ts).total_seconds() / 3600.0, 0)
        mem = mem + np.clip(hrs_since * LEAK_PCT_PER_HR, 0, LEAK_CAP - prof["mem"])

    # Battery: drain while on battery, charge when low
    drain_rate = np.full(n, prof["drain_per_hr"])
    if model == "Model-C" and on_new_build:
        drain_rate = np.where(after, drain_rate * DRAIN_MULTIPLIER, drain_rate)

    battery = np.empty(n)
    level = rng.uniform(60, 100)
    charging = False
    step_h = INTERVAL_MIN / 60.0
    for i in range(n):
        if charging:
            level += 25 * step_h
            if level >= 99:
                charging = False
        else:
            level -= drain_rate[i] * step_h * (0.7 + 0.3 * daily[i]) + rng.normal(0, 0.15)
            if level <= 15:
                charging = True
        level = min(level, 100)
        battery[i] = level

    return pd.DataFrame({
        "timestamp": timestamps,
        "device_id": device_id,
        "hardware_model": model,
        "region": region,
        "cpu_pct": np.clip(cpu, 0, 100).round(2),
        "mem_pct": np.clip(mem, 0, 100).round(2),
        "battery_pct": np.clip(battery, 0, 100).round(2),
    }), after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, default=40)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--rollout-day", type=float, default=3.0, help="Day offset when build-2 rolls out")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--no-dq-faults", action="store_true", help="Disable injected data-quality faults")
    ap.add_argument("--out", default="data/fleet_telemetry.csv")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    end = pd.Timestamp("2026-01-08 00:00:00")
    start = end - pd.Timedelta(days=args.days)
    timestamps = pd.date_range(start, end, freq=f"{INTERVAL_MIN}min", inclusive="left")
    rollout_ts = start + pd.Timedelta(days=args.rollout_day)

    frames = []
    truth_devices = []
    for i in range(args.devices):
        device_id = f"dev-{i:03d}"
        model = MODELS[i % len(MODELS)]
        region = REGIONS[rng.integers(0, len(REGIONS))]
        on_new_build = bool(rng.random() < 0.5)  # half the fleet gets build-2

        df, after = simulate_device(rng, device_id, model, region, timestamps, rollout_ts, on_new_build)
        df["os_build"] = np.where(after, "build-2", "build-1")
        frames.append(df)

        affected = on_new_build and model in ("Model-B", "Model-C")
        truth_devices.append({
            "device_id": device_id, "hardware_model": model, "region": region,
            "upgraded_to_build_2": on_new_build,
            "regression": (
                "memory_leak" if affected and model == "Model-B"
                else "battery_drain" if affected and model == "Model-C"
                else None
            ),
        })

    data = pd.concat(frames, ignore_index=True)
    data = data[["timestamp", "device_id", "os_build", "hardware_model", "region",
                 "cpu_pct", "mem_pct", "battery_pct"]]

    dq = {"dropped_rows": 0, "duplicate_rows": 0, "out_of_range_values": 0}
    if not args.no_dq_faults:
        # Dropped heartbeats (~1%)
        drop_idx = rng.choice(data.index, size=int(len(data) * 0.01), replace=False)
        data = data.drop(drop_idx)
        dq["dropped_rows"] = len(drop_idx)
        # Duplicates (~0.3%)
        dup = data.sample(frac=0.003, random_state=args.seed)
        data = pd.concat([data, dup], ignore_index=True)
        dq["duplicate_rows"] = len(dup)
        # Out-of-range values (~0.1%): impossible percentages
        bad_idx = rng.choice(data.index, size=int(len(data) * 0.001), replace=False)
        data.loc[bad_idx, "cpu_pct"] = rng.choice([-5.0, 150.0, 999.0], size=len(bad_idx))
        dq["out_of_range_values"] = len(bad_idx)

    data = data.sort_values(["timestamp", "device_id"]).reset_index(drop=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(out, index=False)

    truth = {
        "rollout_timestamp": str(rollout_ts),
        "seed": args.seed,
        "interval_minutes": INTERVAL_MIN,
        "regressions": {
            "memory_leak": f"Model-B on build-2 gains ~{LEAK_PCT_PER_HR}% memory/hour after rollout",
            "battery_drain": f"Model-C on build-2 drains {DRAIN_MULTIPLIER}x faster after rollout",
        },
        "data_quality_faults": dq,
        "devices": truth_devices,
    }
    truth_path = out.with_name("ground_truth.json")
    truth_path.write_text(json.dumps(truth, indent=2))

    print(f"Wrote {len(data):,} rows for {args.devices} devices -> {out}")
    print(f"Rollout of build-2 at {rollout_ts}")
    print(f"Affected devices: {sum(1 for d in truth_devices if d['regression'])}")
    print(f"Data-quality faults injected: {dq}")
    print(f"Ground truth -> {truth_path}")


if __name__ == "__main__":
    main()
