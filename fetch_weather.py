"""Fetch Ambient Weather data and store it as one compact JSON file per UTC day.

Credentials come from environment variables (GitHub Actions secrets):
  AWN_API_KEY, AWN_APP_KEY, AWN_MAC
Optional:
  BACKFILL_DAYS  - also page backwards this many days (one-time history load)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

API = "https://rt.ambientweather.net/v1/devices/"
DATA_DIR = Path("data")

# Fields kept in the history files. Add more names here if your station
# reports extra sensors (e.g. "soilmoisture1", "lightning_day", "temp1f").
KEEP = [
    "tempf", "feelsLike", "dewPoint", "humidity",
    "windspeedmph", "windgustmph", "winddir", "maxdailygust",
    "baromrelin", "baromabsin",
    "hourlyrainin", "dailyrainin", "weeklyrainin", "monthlyrainin",
    "solarradiation", "uv",
    "tempinf", "humidityin",
]


def day_of(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def slim(rec):
    t = rec.get("dateutc")
    if not isinstance(t, (int, float)):
        return None
    out = {"t": int(t)}
    for k in KEEP:
        v = rec.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
    return out


def fetch(mac, end_ms=None):
    params = {
        "apiKey": os.environ["AWN_API_KEY"],
        "applicationKey": os.environ["AWN_APP_KEY"],
        "limit": 288,
    }
    if end_ms is not None:
        params["endDate"] = int(end_ms)
    try:
        r = requests.get(API + mac, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except requests.RequestException as e:
        # Don't print the exception text: it contains the request URL (with keys).
        status = getattr(getattr(e, "response", None), "status_code", "n/a")
        sys.exit(f"Request to Ambient Weather failed (HTTP status: {status})")
    if not isinstance(data, list):
        sys.exit(f"Unexpected API response: {str(data)[:200]}")
    return data


def merge(records):
    """Merge records into data/YYYY-MM-DD.json files. Returns count of new rows."""
    by_day = {}
    for rec in records:
        s = slim(rec)
        if s:
            by_day.setdefault(day_of(s["t"]), []).append(s)
    DATA_DIR.mkdir(exist_ok=True)
    added = 0
    for day, recs in by_day.items():
        path = DATA_DIR / f"{day}.json"
        existing = {}
        if path.exists():
            existing = {r["t"]: r for r in json.loads(path.read_text())}
        before = len(existing)
        for r in recs:
            existing[r["t"]] = r
        added += len(existing) - before
        path.write_text(
            json.dumps([existing[k] for k in sorted(existing)], separators=(",", ":"))
        )
    return added


def write_index():
    days = sorted(p.stem for p in DATA_DIR.glob("????-??-??.json"))
    latest = None
    if days:
        rows = json.loads((DATA_DIR / f"{days[-1]}.json").read_text())
        latest = rows[-1] if rows else None
    (DATA_DIR / "index.json").write_text(
        json.dumps({"days": days, "latest": latest}, separators=(",", ":"))
    )


def oldest_ts(records):
    ts = [r["dateutc"] for r in records if isinstance(r.get("dateutc"), (int, float))]
    return min(ts) if ts else None


def main():
    mac = os.environ["AWN_MAC"]
    backfill_days = int(os.environ.get("BACKFILL_DAYS") or 0)

    records = fetch(mac)
    added = merge(records)

    if backfill_days > 0:
        cutoff = time.time() * 1000 - backfill_days * 86_400_000
        oldest = oldest_ts(records)
        for _ in range(backfill_days + 5):
            if oldest is None or oldest <= cutoff:
                break
            time.sleep(1.2)  # AWN allows ~1 request/second per key
            records = fetch(mac, oldest - 1)
            if not records:
                break
            added += merge(records)
            new_oldest = oldest_ts(records)
            if new_oldest is None or new_oldest >= oldest:
                break
            oldest = new_oldest

    write_index()
    print(f"Added {added} new readings.")


if __name__ == "__main__":
    main()
