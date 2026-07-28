#!/usr/bin/env python3
"""Build-time fetch of EMMA awareness-zone geometries → bundled GeoJSON.

Pulls the MeteoAlarm Metadata API region geometries and writes a GeoJSON
FeatureCollection that EmmaZoneIndex consumes at runtime. Run this ONCE (or when
zones change) with a MeteoAlarm API key; commit the produced file into the image.

Usage:
    METEOALARM_API_KEY=... python3 scripts/fetch_emma_zones.py \
        --out weather_worker/data/emma_zones.geojson

The exact Metadata API response schema is confirmed at run time, not guessed:
the script accepts either a GeoJSON FeatureCollection or a list of region objects
that each carry a geometry, and normalises each feature's EMMA id into
`properties.emma_id`. If neither shape is found it saves the raw response next to
--out (`.raw.json`) and exits non-zero so a human can map the fields.

ASSUMPTION: Metadata API base + regions path — confirm against the live API docs
for your key (see internal-docs-local/2026-07-27-emma-zone-source.md).
"""
import argparse
import json
import os
import sys

import requests

# Reuse the runtime normalisation so the bundled file matches what EmmaZoneIndex
# expects (single source of truth for the zone contract).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from weather_worker.emma_zones import normalize_zone_payload  # noqa: E402

DEFAULT_URL = os.getenv(
    "METEOALARM_REGIONS_URL",
    "https://api.meteoalarm.org/metadata/v1/regions",
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="weather_worker/data/emma_zones.geojson")
    ap.add_argument("--url", default=DEFAULT_URL)
    args = ap.parse_args()

    key = os.getenv("METEOALARM_API_KEY")
    if not key:
        print("ERROR: set METEOALARM_API_KEY", file=sys.stderr)
        return 2

    resp = requests.get(
        args.url,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()

    try:
        fc = normalize_zone_payload(payload)
    except ValueError as e:
        raw = args.out + ".raw.json"
        os.makedirs(os.path.dirname(raw) or ".", exist_ok=True)
        with open(raw, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"ERROR: {e}. Raw response saved to {raw} — map fields and retry.",
              file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(fc, fh)
    print(f"Wrote {len(fc['features'])} zones → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
