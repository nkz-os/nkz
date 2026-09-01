"""Tests for ParcelWeatherEngine — parcel-driven weather ingestion."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "weather-worker"))

import pytest
from unittest.mock import MagicMock, patch, call
from weather_worker.parcel_engine import ParcelWeatherEngine


class TestParcelDiscovery:
    """Parcel discovery from Orion-LD."""

    @patch("weather_worker.parcel_engine.requests.get")
    def test_fetch_all_parcels_single_tenant(self, mock_get):
        """Fetch AgriParcel entities for a single tenant."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Uniform writes: ids are urn:ngsi-ld:AgriParcel:<uuid4> (no tenant segment).
        # The tenant is resolved by discovery (env/DB) and the per-tenant query, NOT
        # parsed from the id — so _fetch_all_parcels tags _tenant from the queried tenant.
        parcel1_id = "urn:ngsi-ld:AgriParcel:11111111-1111-1111-1111-111111111111"
        mock_response.json.return_value = [
            {
                "id": parcel1_id,
                "type": "AgriParcel",
                "name": {"type": "Property", "value": "Viñedo Norte"},
                "location": {
                    "type": "GeoProperty",
                    "value": {
                        "type": "Point",
                        "coordinates": [-1.6458, 42.8125],
                    },
                },
                "elevation": {"type": "Property", "value": 450.0},
            },
            {
                "id": "urn:ngsi-ld:AgriParcel:22222222-2222-2222-2222-222222222222",
                "type": "AgriParcel",
                "name": {"type": "Property", "value": "Trigal Sur"},
                "location": {
                    "type": "GeoProperty",
                    "value": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [-1.6400, 42.8100],
                                [-1.6300, 42.8100],
                                [-1.6300, 42.8050],
                                [-1.6400, 42.8050],
                                [-1.6400, 42.8100],
                            ]
                        ],
                    },
                },
            },
        ]
        mock_get.return_value = mock_response

        with patch.object(
            ParcelWeatherEngine, "_get_active_tenants", return_value=["tenant1"]
        ):
            engine = ParcelWeatherEngine(
                orion_url="http://orion:1026",
                openmeteo_url="https://api.open-meteo.com/v1",
            )
            parcels = engine._fetch_all_parcels()

        assert len(parcels) == 2
        assert parcels[0]["id"] == parcel1_id
        assert parcels[0]["_tenant"] == "tenant1"

    @patch("weather_worker.parcel_engine.requests.get")
    def test_fetch_all_parcels_multi_tenant(self, mock_get):
        """Fetch AgriParcel entities across all active tenants."""
        mock_parcels_t1 = MagicMock()
        mock_parcels_t1.status_code = 200
        mock_parcels_t1.json.return_value = [
            {
                "id": "urn:ngsi-ld:AgriParcel:t1:p1",
                "type": "AgriParcel",
                "location": {
                    "type": "GeoProperty",
                    "value": {"type": "Point", "coordinates": [-1.6, 42.8]},
                },
            }
        ]
        mock_parcels_t2 = MagicMock()
        mock_parcels_t2.status_code = 200
        mock_parcels_t2.json.return_value = [
            {
                "id": "urn:ngsi-ld:AgriParcel:t2:p2",
                "type": "AgriParcel",
                "location": {
                    "type": "GeoProperty",
                    "value": {"type": "Point", "coordinates": [-1.7, 42.9]},
                },
            }
        ]
        mock_get.side_effect = [mock_parcels_t1, mock_parcels_t2]

        with patch.object(
            ParcelWeatherEngine, "_get_active_tenants", return_value=["tenant1", "tenant2"]
        ):
            engine = ParcelWeatherEngine(
                orion_url="http://orion:1026",
                openmeteo_url="https://api.open-meteo.com/v1",
            )
            parcels = engine._fetch_all_parcels()

        assert len(parcels) == 2
        tenant_ids = {p["_tenant"] for p in parcels}
        assert tenant_ids == {"tenant1", "tenant2"}

    @patch("weather_worker.parcel_engine.requests.get")
    def test_fetch_returns_empty_on_404(self, mock_get):
        """Empty result when no parcels exist."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        with patch.object(
            ParcelWeatherEngine, "_get_active_tenants", return_value=["tenant1"]
        ):
            engine = ParcelWeatherEngine(
                orion_url="http://orion:1026",
                openmeteo_url="https://api.open-meteo.com/v1",
            )
            parcels = engine._fetch_all_parcels()

        assert parcels == []


class TestSpatialClustering:
    """Spatial clustering to minimize API calls."""

    def test_cluster_nearby_parcels(self):
        """Parcels within 2km are grouped together."""
        engine = ParcelWeatherEngine(
            orion_url="http://orion:1026",
            openmeteo_url="https://api.open-meteo.com/v1",
        )

        parcels = [
            {
                "id": "p1",
                "_tenant": "t1",
                "_centroid": (-1.6458, 42.8125),
                "_altitude": 450.0,
            },
            {
                "id": "p2",
                "_tenant": "t1",
                "_centroid": (-1.6460, 42.8130),  # ~100m from p1
                "_altitude": 460.0,
            },
            {
                "id": "p3",
                "_tenant": "t1",
                "_centroid": (-1.6000, 42.9000),  # ~10km from p1
                "_altitude": 800.0,
            },
        ]

        clusters = engine._cluster_parcels(parcels, radius_km=2.0)

        assert len(clusters) == 2
        cluster_sizes = [len(c) for c in clusters]
        assert sorted(cluster_sizes) == [1, 2]

    def test_cluster_empty_list(self):
        """Empty parcel list returns empty clusters."""
        engine = ParcelWeatherEngine(
            orion_url="http://orion:1026",
            openmeteo_url="https://api.open-meteo.com/v1",
        )
        clusters = engine._cluster_parcels([], radius_km=2.0)
        assert clusters == []


class TestCentroidExtraction:
    """Extract centroid and altitude from parcel entities."""

    def test_extract_point_centroid(self):
        """Point geometry: direct coordinates."""
        engine = ParcelWeatherEngine(
            orion_url="http://orion:1026",
            openmeteo_url="https://api.open-meteo.com/v1",
        )

        parcel = {
            "id": "p1",
            "location": {
                "type": "GeoProperty",
                "value": {"type": "Point", "coordinates": [-1.6458, 42.8125]},
            },
            "elevation": {"type": "Property", "value": 450.0},
        }

        centroid, altitude = engine._extract_centroid_and_altitude(parcel)

        assert centroid == (-1.6458, 42.8125)
        assert altitude == 450.0

    def test_extract_polygon_centroid(self):
        """Polygon geometry: calculate centroid."""
        engine = ParcelWeatherEngine(
            orion_url="http://orion:1026",
            openmeteo_url="https://api.open-meteo.com/v1",
        )

        parcel = {
            "id": "p2",
            "location": {
                "type": "GeoProperty",
                "value": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-1.64, 42.81],
                            [-1.63, 42.81],
                            [-1.63, 42.80],
                            [-1.64, 42.80],
                            [-1.64, 42.81],
                        ]
                    ],
                },
            },
        }

        centroid, altitude = engine._extract_centroid_and_altitude(parcel)

        assert centroid is not None
        assert abs(centroid[0] - (-1.635)) < 0.01
        assert abs(centroid[1] - 42.805) < 0.01
        assert altitude == 0.0  # no elevation attribute

    def test_extract_no_location_returns_none(self):
        """Parcel without location returns None centroid."""
        engine = ParcelWeatherEngine(
            orion_url="http://orion:1026",
            openmeteo_url="https://api.open-meteo.com/v1",
        )

        parcel = {"id": "p3", "type": "AgriParcel"}

        centroid, altitude = engine._extract_centroid_and_altitude(parcel)

        assert centroid is None


class TestDbTenantDiscoveryQuery:
    """The SQL query must use the real tenant_installed_modules column (module_id),
    not a column that doesn't exist (module_name) — that bug silently broke DB
    discovery for every tenant except whatever PARCEL_ENGINE_TENANTS hardcoded."""

    @patch("psycopg2.connect")
    def test_discover_uses_module_id_column(self, mock_connect, monkeypatch):
        monkeypatch.setenv("POSTGRES_URL", "postgresql://u:p@h:5432/db")
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("montiko",), ("asociacion-allotarra",)]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = ParcelWeatherEngine()
        tenants = engine._discover_tenants_from_db()

        assert tenants == ["montiko", "asociacion-allotarra"]
        executed_sql = mock_cursor.execute.call_args[0][0]
        assert "module_id" in executed_sql
        assert "module_name" not in executed_sql


def test_write_weather_forecast_upserts_the_entity(monkeypatch):
    """El forecast se publica en el mismo ciclo que la observación.

    Un ciclo que escriba la observación y no el forecast deja a weather-map sin
    tMin/tMax, que es exactamente lo que FAO-56 necesita.
    """
    from weather_worker.parcel_engine import ParcelWeatherEngine

    engine = ParcelWeatherEngine(orion_url="http://orion:1026")
    sent = {}

    class _Resp:
        status_code = 204
        text = ""

    def _post(url, json=None, headers=None, timeout=None, **kwargs):
        sent["url"] = url
        sent["body"] = json
        return _Resp()

    monkeypatch.setattr("weather_worker.parcel_engine.requests.post", _post)

    ok = engine._write_weather_forecast(
        tenant_id="t",
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        location=(-2.07, 42.63, 450.0),
        daily={"temp_min": 9.9, "temp_max": 28.4, "precip_probability": 20.0},
    )

    assert ok is True
    assert "entityOperations/upsert" in sent["url"]
    assert sent["body"][0]["type"] == "WeatherForecast"
    assert sent["body"][0]["dayMaximum"]["value"]["temperature"] == 28.4


print("All parcel engine tests passed.")
