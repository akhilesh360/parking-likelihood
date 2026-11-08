"""Fetch live snapshots of City of Melbourne on-street parking bay sensors.

Usage: python src/fetch.py [--snapshots N] [--every MINUTES]
Each snapshot is appended to data/snapshots.csv (one row per bay per snapshot).
"""
import argparse, csv, os, time
from datetime import datetime, timezone
import requests

BASE = "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "snapshots.csv")
FIELDS = ["snapshot_utc", "kerbsideid", "zone_number", "status", "status_timestamp", "lat", "lon"]


def fetch_bays():
    url = f"{BASE}/on-street-parking-bay-sensors/exports/json"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_streets():
    """Zone -> street name, so areas read like 'Lygon St (Argyle Pl N - Grattan St)'."""
    r = requests.get(f"{BASE}/parking-zones-linked-to-street-segments/exports/json", timeout=60)
    r.raise_for_status()
    return r.json()


def snapshot():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = fetch_bays()
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        for b in rows:
            loc = b.get("location") or {}
            w.writerow({"snapshot_utc": now, "kerbsideid": b.get("kerbsideid"),
                        "zone_number": b.get("zone_number"), "status": b.get("status_description"),
                        "status_timestamp": b.get("status_timestamp"),
                        "lat": loc.get("lat"), "lon": loc.get("lon")})
    print(f"{now}: {len(rows)} bays")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=int, default=1)
    ap.add_argument("--every", type=float, default=10)
    a = ap.parse_args()
    import json
    sp = os.path.join(os.path.dirname(OUT), "streets.json")
    if not os.path.exists(sp):
        json.dump(fetch_streets(), open(sp, "w"))
    for i in range(a.snapshots):
        snapshot()
        if i < a.snapshots - 1:
            time.sleep(a.every * 60)
