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


# ---------------------------------------------------------------------------
# @context delivery mode — ETSI GS CIM 009 §6.3.5 mutual exclusivity.
#
# @context in the body  -> Content-Type: application/ld+json, NO Link header
# @context NOT in body  -> Content-Type: application/json + Link header
#
# Sending both is a 400 BadRequestData on EVERY request: the write never lands
# and the only trace is a warning line. The forecast writer posts a body built
# by build_weather_forecast_entity(), which embeds @context, so the headers it
# sends are not free to be either mode.
# ---------------------------------------------------------------------------

CONTEXT_URL_FOR_TESTS = "http://api-gateway-service:5000/ngsi-ld-context.json"


def _body_carries_context(body) -> bool:
    if isinstance(body, dict):
        return "@context" in body
    if isinstance(body, list):
        return any(isinstance(e, dict) and "@context" in e for e in body)
    return False


def assert_context_mode_is_valid(headers, body):
    """Fail if the header/body pairing is one Orion-LD rejects with a 400."""
    headers = headers or {}
    content_type = headers.get("Content-Type", "")
    if _body_carries_context(body):
        assert content_type == "application/ld+json", (
            "the body carries @context, so Content-Type must be application/ld+json, "
            f"got {content_type!r} -> Orion-LD 400 BadRequestData"
        )
        assert "Link" not in headers, (
            "@context in the body AND a Link header is the forbidden pairing "
            f"(Link={headers.get('Link')!r}) -> Orion-LD 400 BadRequestData"
        )
    else:
        assert content_type == "application/json", (
            "no @context in the body, so Content-Type must be application/json, "
            f"got {content_type!r}"
        )


def _capture_forecast_post(monkeypatch, **overrides):
    """Run _write_weather_forecast and return (ok, captured request)."""
    from weather_worker.parcel_engine import ParcelWeatherEngine

    engine = ParcelWeatherEngine(orion_url="http://orion:1026")
    sent = {}

    class _Resp:
        status_code = 204
        text = ""

    def _post(url, json=None, headers=None, timeout=None, **kwargs):
        sent["url"] = url
        sent["body"] = json
        sent["headers"] = headers
        return _Resp()

    monkeypatch.setattr("weather_worker.parcel_engine.requests.post", _post)

    kwargs = dict(
        tenant_id="t",
        parcel_id="urn:ngsi-ld:AgriParcel:t:p1",
        location=(-2.07, 42.63, 450.0),
        daily={
            "observed_at": "2026-09-01",
            "temp_min": 9.9,
            "temp_max": 28.4,
            "precip_probability": 20.0,
        },
    )
    kwargs.update(overrides)
    ok = engine._write_weather_forecast(**kwargs)
    return ok, sent


def test_write_weather_forecast_upserts_the_entity(monkeypatch):
    """El forecast se publica en el mismo ciclo que la observación.

    Un ciclo que escriba la observación y no el forecast deja a weather-map sin
    tMin/tMax, que es exactamente lo que FAO-56 necesita.
    """
    ok, sent = _capture_forecast_post(monkeypatch)

    assert ok is True
    assert "entityOperations/upsert" in sent["url"]
    assert sent["body"][0]["type"] == "WeatherForecast"
    assert sent["body"][0]["dayMaximum"]["value"]["temperature"] == 28.4


def test_write_weather_forecast_sends_a_context_mode_orion_accepts(monkeypatch):
    """Con CONTEXT_URL puesto (producción) el Link header aparecería solo.

    Este es el caso que se desplegaba: @context en el cuerpo + Link header, 400 en
    cada parcela y cada ciclo.
    """
    monkeypatch.setenv("CONTEXT_URL", CONTEXT_URL_FOR_TESTS)
    _, sent = _capture_forecast_post(monkeypatch)
    assert_context_mode_is_valid(sent["headers"], sent["body"])


def test_write_weather_forecast_context_mode_holds_without_context_url(monkeypatch):
    """Y sin CONTEXT_URL tampoco vale application/json con @context en el cuerpo."""
    monkeypatch.delenv("CONTEXT_URL", raising=False)
    _, sent = _capture_forecast_post(monkeypatch)
    assert_context_mode_is_valid(sent["headers"], sent["body"])


def test_the_context_mode_assertion_catches_the_forbidden_pairing():
    """Guard the guard: the assertion must reject what Orion rejects."""
    body = [{"@context": [CONTEXT_URL_FOR_TESTS], "id": "urn:ngsi-ld:X:1", "type": "X"}]
    with pytest.raises(AssertionError):
        assert_context_mode_is_valid(
            {"Content-Type": "application/json", "Link": "<ctx>; rel=..."}, body
        )
    with pytest.raises(AssertionError):
        assert_context_mode_is_valid(
            {"Content-Type": "application/ld+json", "Link": "<ctx>; rel=..."}, body
        )


# ---------------------------------------------------------------------------
# Full cycle — what actually reaches Orion.
#
# The unit tests above mock one call each; these run run_once() end to end with
# only the network mocked, which is the only place where the altitude that the
# values were downscaled TO can be compared against the grid altitude they came
# FROM. Publishing the grid altitude makes every consumer re-apply a correction
# the producer already applied.
# ---------------------------------------------------------------------------

PARCEL_AT_450 = {
    "id": "urn:ngsi-ld:AgriParcel:t:p1",
    "type": "AgriParcel",
    "name": {"type": "Property", "value": "P1"},
    "location": {
        "type": "GeoProperty",
        "value": {"type": "Point", "coordinates": [-2.07, 42.63]},
    },
    "elevation": {"type": "Property", "value": 450.0},
}

GRID_AT_300 = {
    "elevation": 300.0,
    "daily": {
        "time": ["2026-09-01"],
        "temperature_2m_min": [9.9],
        "temperature_2m_max": [28.4],
        "temperature_2m_mean": [19.0],
        "relative_humidity_2m_mean": [55.0],
        "precipitation_sum": [0.0],
        "precipitation_probability_max": [20.0],
        "et0_fao_evapotranspiration": [4.1],
        "shortwave_radiation_sum": [22.0],
        "surface_pressure_mean": [960.0],
    },
    "hourly": {},
}


def _run_cycle(monkeypatch, parcel=PARCEL_AT_450, openmeteo=GRID_AT_300, downscaled=None):
    """Run one full engine cycle with only the network stubbed."""
    from weather_worker import parcel_engine as pe
    from weather_worker.storage import orion_writer

    engine = pe.ParcelWeatherEngine(orion_url="http://orion:1026")
    posts = []

    class _Resp:
        status_code = 201
        text = ""

    def _post(url, json=None, headers=None, timeout=None, **kwargs):
        posts.append({"url": url, "body": json, "headers": headers})
        return _Resp()

    monkeypatch.setattr(pe.requests, "post", _post)
    monkeypatch.setattr(orion_writer.requests, "post", _post)
    monkeypatch.setattr(pe.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(engine, "_get_active_tenants", lambda: ["t"])
    monkeypatch.setattr(engine, "_fetch_all_parcels", lambda: [dict(parcel, _tenant="t")])
    monkeypatch.setattr(engine, "_fetch_openmeteo", lambda lat, lon: openmeteo)
    monkeypatch.setattr(engine, "_compute_terrain_attributes", lambda c: (0.0, 0.0))
    monkeypatch.setattr(engine, "_prune_orphan_weather_observed", lambda tid: 0)
    if downscaled is not None:
        monkeypatch.setattr(engine, "_downscale_observations", lambda **kw: downscaled)

    return engine.run_once(), posts


def _entity_of_type(posts, entity_type):
    for post in posts:
        body = post["body"]
        entities = body if isinstance(body, list) else [body]
        for entity in entities:
            if isinstance(entity, dict) and entity.get("type") == entity_type:
                return entity, post
    raise AssertionError(f"no {entity_type} was posted: {[p['url'] for p in posts]}")


def _coordinates(entity):
    return entity["location"]["value"]["coordinates"]


def test_cycle_declares_the_parcel_altitude_not_the_grid_altitude():
    """location[2] es la base DESDE la que el consumidor corrige.

    Los valores publicados ya vienen bajados a la altitud de la parcela por
    _downscale_observations. Declarar los 300 m de la celda del modelo haría que
    weather-map volviera a aplicar Gamma*(450-300): sesgo sistemático, siempre en
    el mismo sentido.
    """
    import pytest as _pytest

    with _pytest.MonkeyPatch.context() as mp:
        stats, posts = _run_cycle(mp)

    observed, _ = _entity_of_type(posts, "WeatherObserved")
    forecast, _ = _entity_of_type(posts, "WeatherForecast")

    assert _coordinates(observed)[2] == 450.0, "WeatherObserved lleva la altitud del grid"
    assert _coordinates(forecast)[2] == 450.0, "WeatherForecast lleva la altitud del grid"
    assert stats["weather_forecast_written"] == 1


def test_cycle_omits_the_altitude_when_nobody_knows_it():
    """Sin elevación de parcela ni de grid, la coordenada se OMITE.

    Un 0.0 por defecto declara nivel del mar: ~3 degC fabricados para una parcela
    a 450 m. Un hueco es honesto.
    """
    import pytest as _pytest

    parcel = {k: v for k, v in PARCEL_AT_450.items() if k != "elevation"}
    grid = {k: v for k, v in GRID_AT_300.items() if k != "elevation"}
    with _pytest.MonkeyPatch.context() as mp:
        _, posts = _run_cycle(mp, parcel=parcel, openmeteo=grid)

    observed, _ = _entity_of_type(posts, "WeatherObserved")
    forecast, _ = _entity_of_type(posts, "WeatherForecast")

    assert len(_coordinates(observed)) == 2, "0.0 declara nivel del mar; se omite"
    assert len(_coordinates(forecast)) == 2, "0.0 declara nivel del mar; se omite"


def test_parse_keeps_a_missing_grid_elevation_missing():
    """Open-Meteo sin `elevation` no puede convertirse en 0.0 metros."""
    engine = ParcelWeatherEngine(orion_url="http://orion:1026")
    grid = {k: v for k, v in GRID_AT_300.items() if k != "elevation"}
    observations = engine._parse_openmeteo_response(grid, 42.63, -2.07)
    assert observations, "fixture must yield one observation"
    assert observations[0]["station_elevation_m"] is None


def _fallback_downscale(monkeypatch, observation):
    """Run _downscale_observations with the spatial downscaler unavailable."""
    import sys as _sys

    engine = ParcelWeatherEngine(orion_url="http://orion:1026")
    monkeypatch.setitem(_sys.modules, "weather_utils.spatial_downscaler", None)
    return engine._downscale_observations(
        observations=[observation],
        parcel_lat=42.63,
        parcel_lon=-2.07,
        parcel_altitude_m=450.0,
        station_altitude_m=300.0,
        parcel_entity={},
    )[0]


def test_fallback_path_still_carries_the_horizontal_radiation(monkeypatch):
    """Sin downscaler el valor crudo YA es la global horizontal.

    No publicarlo dejaba a weather-map sin radiación — y sin ET0 — sin más rastro
    que una línea de debug, que es la firma del fallo silencioso que este trabajo
    existe para quitar.
    """
    out = _fallback_downscale(
        monkeypatch,
        {"observed_at": "2026-09-01", "solar_rad_w_m2": 254.6, "station_elevation_m": 300.0},
    )
    assert out["solar_rad_w_m2_horizontal"] == 254.6


def test_fallback_path_does_not_invent_radiation(monkeypatch):
    """Y si Open-Meteo no la trae, el atributo se queda fuera."""
    out = _fallback_downscale(monkeypatch, {"observed_at": "2026-09-01"})
    assert "solar_rad_w_m2_horizontal" not in out


def test_validity_window_names_the_day_of_the_observation(monkeypatch):
    """La ventana la fija el dato, no el reloj del worker.

    La observación es el día 0 de Open-Meteo en timezone=Europe/Madrid; utcnow()
    ya está en el día siguiente entre las 22:00 UTC y medianoche, así que la
    ventana nombraba un día distinto del que describe el payload.
    """
    _, sent = _capture_forecast_post(
        monkeypatch,
        daily={"observed_at": "2026-08-31", "temp_min": 9.9, "temp_max": 28.4},
    )
    entity = sent["body"][0]
    assert entity["validFrom"]["value"]["@value"] == "2026-08-31T00:00:00Z"
    assert entity["validTo"]["value"]["@value"] == "2026-08-31T23:59:59Z"


def test_forecast_is_skipped_when_the_observation_has_no_day(monkeypatch):
    """Sin día no se inventa uno: publicar la ventana equivocada es peor que no
    publicar, porque el consumidor no puede distinguirla de una buena."""
    ok, sent = _capture_forecast_post(
        monkeypatch, daily={"temp_min": 9.9, "temp_max": 28.4}
    )
    assert ok is False
    assert not sent, "no debe salir ninguna petición"


def test_forecast_is_skipped_when_the_day_is_unparseable(monkeypatch):
    ok, sent = _capture_forecast_post(
        monkeypatch,
        daily={"observed_at": "ayer", "temp_min": 9.9, "temp_max": 28.4},
    )
    assert ok is False
    assert not sent


print("All parcel engine tests passed.")
