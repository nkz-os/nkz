#!/usr/bin/env python3
"""Backfill catalog_municipalities coordinates from AEMET /maestro/municipios.

The AEMET municipality master list carries ``latitud_dec``/``longitud_dec`` for
all ~8122 Spanish municipalities. The legacy populate script only mapped the INE
dataset (names + codes, no coordinates) and geocoded a small subset via
Nominatim, leaving 7861 rows with NULL coordinates and a few with wrong
geocodes. This script maps the AEMET ``id`` (``id`` + 5-digit INE code) to the
INE code and backfills ``latitude``/``longitude``/``geom``.

Usage:
    AEMET_API_KEY=... python3 backfill-municipality-coordinates.py
"""
import os
import sys

import psycopg2
import requests


def fetch_aemet_municipalities() -> dict:
    key = os.environ.get("AEMET_API_KEY")
    if not key:
        print("AEMET_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    resp = requests.get(
        "https://opendata.aemet.es/opendata/api/maestro/municipios",
        headers={"api_key": key},
        timeout=30,
    )
    resp.raise_for_status()
    data_url = resp.json().get("datos")
    if not data_url:
        raise RuntimeError("AEMET response has no data URL")

    data_resp = requests.get(data_url, timeout=60)
    data_resp.encoding = "latin-1"  # AEMET serves this file as latin-1
    rows = data_resp.json()

    mapping: dict = {}
    for m in rows:
        aemet_id = m.get("id") or ""
        lat = m.get("latitud_dec")
        lon = m.get("longitud_dec")
        if not aemet_id.startswith("id") or lat is None or lon is None:
            continue
        # AEMET id is "id" + 5-digit INE code (e.g. "id44001" -> 44001).
        ine_code = aemet_id[2:]
        try:
            mapping[ine_code] = (float(lat), float(lon))
        except (TypeError, ValueError):
            continue
    return mapping


def main() -> None:
    postgres_url = os.environ.get("POSTGRES_URL")
    if not postgres_url:
        print("POSTGRES_URL not set", file=sys.stderr)
        sys.exit(1)

    mapping = fetch_aemet_municipalities()
    print(f"Fetched {len(mapping)} municipalities with coordinates from AEMET")

    conn = psycopg2.connect(postgres_url)
    conn.autocommit = False
    updated = 0
    try:
        with conn.cursor() as cur:
            for ine_code, (lat, lon) in mapping.items():
                cur.execute(
                    """
                    UPDATE catalog_municipalities
                    SET latitude = %s,
                        longitude = %s,
                        geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    WHERE ine_code = %s
                    """,
                    (lat, lon, lon, lat, ine_code),
                )
                updated += cur.rowcount
        conn.commit()
    finally:
        conn.close()

    print(f"Backfilled {updated} municipalities")


if __name__ == "__main__":
    main()
