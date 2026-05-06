#!/usr/bin/env python3
"""
Weather Blueprint - Extracted from entity_management_api.py
"""
import os
import sys
import json
import logging
import math
import re
from typing import Optional
from datetime import datetime, timedelta

from flask import Blueprint, request, jsonify, g, Response
from psycopg2.extras import RealDictCursor
import requests

from common.auth_middleware import require_auth, inject_fiware_headers
from db_helper import get_db_connection_with_tenant, get_db_connection_simple

logger = logging.getLogger(__name__)

# CORS origins for weather preflight
_cors_env = os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://localhost:5173')
ALLOWED_ORIGINS = {o.strip() for o in _cors_env.split(',') if o.strip()}

ORION_URL = os.getenv('ORION_URL')


def get_platform_credential(credential_name: str) -> Optional[str]:
    """Get platform credential from environment variable"""
    return os.getenv(credential_name)


weather_bp = Blueprint('weather', __name__)

# === Lines 186-208 from entity_management_api.py ===
@weather_bp.route('/api/weather/<path:subpath>', methods=['OPTIONS'])
def weather_cors_preflight(subpath):
    """Explicit OPTIONS handler for all /api/weather/* routes to ensure CORS headers"""
    origin = request.headers.get('Origin')
    requested_method = request.headers.get('Access-Control-Request-Method', 'GET')
    requested_headers = request.headers.get('Access-Control-Request-Headers', '')
    logger.info(f"[CORS Preflight] OPTIONS /api/weather/{subpath}, origin={origin}, method={requested_method}, headers={requested_headers}")
    
    # Create response with explicit headers
    resp = Response(response='{}', status=200, mimetype='application/json')
    
    if origin and origin in ALLOWED_ORIGINS:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Vary'] = 'Origin'
        resp.headers['Access-Control-Allow-Credentials'] = 'true'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Tenant-ID, x-tenant-id, X-Auth-Signature'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PATCH'
        resp.headers['Access-Control-Max-Age'] = '86400'  # 24 hours
        logger.info(f"[CORS Preflight] Headers set: Allow-Origin={resp.headers.get('Access-Control-Allow-Origin')}, Allow-Headers={resp.headers.get('Access-Control-Allow-Headers')}, Allow-Methods={resp.headers.get('Access-Control-Allow-Methods')}")
    else:
        logger.warning(f"[CORS Preflight] Origin {origin} not in ALLOWED_ORIGINS: {ALLOWED_ORIGINS}")
    
    return resp

# === Lines 2384-2430 from entity_management_api.py ===
def geocode_municipality_on_demand(name: str, province: Optional[str] = None, ine_code: Optional[str] = None, country: str = 'Spain') -> Optional[tuple]:
    """
    Geocode a municipality using Nominatim (OpenStreetMap) on-demand
    Returns (latitude, longitude) or None if not found
    This is used for lazy geocoding when a municipality is searched but has no coordinates
    """
    try:
        # Build query: "Municipality Name, Province, Spain"
        query_parts = [name]
        if province:
            query_parts.append(province)
        query_parts.append(country)
        query = ', '.join(query_parts)
        
        params = {
            'q': query,
            'format': 'json',
            'limit': 1,
            'countrycodes': 'es',  # Restrict to Spain
        }
        
        headers = {
            'User-Agent': 'Nekazari-Platform/1.0 (Weather Service)',  # Required by Nominatim
        }
        
        # Use a short timeout for on-demand geocoding (don't block user requests)
        response = requests.get('https://nominatim.openstreetmap.org/search', params=params, headers=headers, timeout=5)
        response.raise_for_status()
        
        data = response.json()
        if data and len(data) > 0:
            result = data[0]
            lat = float(result.get('lat', 0))
            lon = float(result.get('lon', 0))
            if lat != 0 and lon != 0:
                logger.info(f"Geocoded '{name}' ({ine_code}): {lat}, {lon}")
                return (lat, lon)
        
        logger.warning(f"Could not geocode municipality '{name}' ({ine_code})")
        return None
        
    except requests.exceptions.Timeout:
        logger.warning(f"Geocoding timeout for '{name}' ({ine_code})")
        return None
    except Exception as e:
        logger.warning(f"Geocoding error for '{name}' ({ine_code}): {e}")
        return None

# === Lines 2432-2633 from entity_management_api.py ===
@weather_bp.route('/api/weather/municipalities/search', methods=['GET'])
@require_auth(require_hmac=False)  # Public read-only endpoint, no HMAC required
def search_municipalities():
    """Search municipalities in catalog (supports AEMET/INE codes and names)"""
    logger.info(f"=== SEARCH MUNICIPALITIES ENDPOINT CALLED ===")
    logger.info(f"Request method: {request.method}")
    logger.info(f"Request path: {request.path}")
    logger.info(f"Request args: {dict(request.args)}")
    logger.info(f"Authorization header present: {bool(request.headers.get('Authorization'))}")
    try:
        tenant_id = g.tenant
        query = request.args.get('q', '').strip()
        limit = int(request.args.get('limit', '20'))
        
        logger.info(f"Searching municipalities: query='{query}', tenant={tenant_id}, limit={limit}")
        
        if not query or len(query) < 2:
            logger.debug("Query too short, returning empty")
            return jsonify({'municipalities': []}), 200
        
        with get_db_connection_with_tenant(tenant_id) as conn:
            if not conn:
                return jsonify({'error': 'Database connection error'}), 500
            
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                
                # 1. First, search in local catalog
                search_term = f'%{query}%'
                cur.execute("""
                    SELECT 
                        ine_code,
                        name,
                        province,
                        autonomous_community,
                        aemet_id,
                        latitude,
                        longitude
                    FROM catalog_municipalities
                    WHERE 
                        LOWER(name) LIKE LOWER(%s)
                        OR ine_code LIKE %s
                        OR LOWER(province) LIKE LOWER(%s)
                    ORDER BY 
                        CASE 
                            WHEN LOWER(name) = LOWER(%s) THEN 1
                            WHEN LOWER(name) LIKE LOWER(%s) THEN 2
                            WHEN ine_code = %s THEN 3
                            ELSE 4
                        END,
                        name ASC
                    LIMIT %s
                """, (search_term, search_term, search_term, query, f'{query}%', query, limit))
                
                municipalities = cur.fetchall()
                logger.info(f"Found {len(municipalities)} municipalities in local catalog for query '{query}'")
                
                # 2. If no results and AEMET API key is available, try to fetch from AEMET
                if not municipalities:
                    aemet_api_key = get_platform_credential('AEMET_API_KEY')
                    if aemet_api_key:
                        try:
                            logger.info(f"Local catalog empty for '{query}', trying AEMET API")
                            aemet_url = "https://opendata.aemet.es/opendata/api/maestro/municipios"
                            headers = {'api_key': aemet_api_key}
                            aemet_response = requests.get(aemet_url, headers=headers, timeout=10)
                            aemet_response.raise_for_status()
                            
                            data_url = aemet_response.json().get('datos')
                            if data_url:
                                data_response = requests.get(data_url, timeout=30)
                                data_response.raise_for_status()
                                aemet_data = data_response.json()
                                
                                # Filter and insert matching municipalities
                                found_municipalities = []
                                for muni in aemet_data:
                                    muni_name = muni.get('nombre', '').lower()
                                    muni_id = muni.get('id', '')
                                    
                                    if query.lower() in muni_name or query in muni_id:
                                        # Insert into catalog if not exists
                                        cur.execute("""
                                            INSERT INTO catalog_municipalities 
                                            (ine_code, name, province, aemet_id, latitude, longitude, geom)
                                            VALUES (%s, %s, %s, %s, %s, %s, 
                                                CASE 
                                                    WHEN %s IS NOT NULL AND %s IS NOT NULL 
                                                    THEN ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                                                    ELSE NULL
                                                END)
                                            ON CONFLICT (ine_code) DO UPDATE SET
                                                name = EXCLUDED.name,
                                                province = EXCLUDED.province,
                                                aemet_id = EXCLUDED.aemet_id,
                                                latitude = EXCLUDED.latitude,
                                                longitude = EXCLUDED.longitude,
                                                geom = CASE 
                                                    WHEN EXCLUDED.longitude IS NOT NULL AND EXCLUDED.latitude IS NOT NULL 
                                                    THEN ST_SetSRID(ST_MakePoint(EXCLUDED.longitude, EXCLUDED.latitude), 4326)
                                                    ELSE catalog_municipalities.geom
                                                END
                                            RETURNING ine_code, name, province, autonomous_community, aemet_id, latitude, longitude
                                        """, (
                                            muni.get('id'),
                                            muni.get('nombre'),
                                            muni.get('provincia'),
                                            muni.get('idAEMET'),
                                            muni.get('latitud_dec'),
                                            muni.get('longitud_dec'),
                                            muni.get('longitud_dec'),
                                            muni.get('latitud_dec'),
                                            muni.get('longitud_dec'),
                                            muni.get('latitud_dec'),
                                        ))
                                        inserted = cur.fetchone()
                                        if inserted:
                                            found_municipalities.append(dict(inserted))
                                        
                                        if len(found_municipalities) >= limit:
                                            break
                                
                                conn.commit()
                                if found_municipalities:
                                    municipalities = found_municipalities
                                    logger.info(f"Found {len(found_municipalities)} municipalities from AEMET for '{query}'")
                        except Exception as e:
                            logger.warning(f"Error fetching from AEMET: {e}")
                            conn.rollback()
                
                # 3. Geocode municipalities without coordinates (on-demand geocoding)
                geocoded_count = 0
                for mun in municipalities:
                    if not mun.get('latitude') or not mun.get('longitude'):
                        try:
                            coords = geocode_municipality_on_demand(
                                name=mun.get('name', ''),
                                province=mun.get('province'),
                                ine_code=mun.get('ine_code')
                            )
                            if coords:
                                lat, lon = coords
                                # Update municipality with coordinates
                                cur.execute("""
                                    UPDATE catalog_municipalities
                                    SET latitude = %s,
                                        longitude = %s,
                                        geom = ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                                    WHERE ine_code = %s
                                    RETURNING latitude, longitude
                                """, (lat, lon, lon, lat, mun.get('ine_code')))
                                updated = cur.fetchone()
                                if updated:
                                    mun['latitude'] = updated['latitude']
                                    mun['longitude'] = updated['longitude']
                                    geocoded_count += 1
                                    logger.info(f"Geocoded municipality {mun.get('name')} ({mun.get('ine_code')}): {lat}, {lon}")
                        except Exception as e:
                            logger.warning(f"Error geocoding municipality {mun.get('ine_code')}: {e}")
                            # Continue with other municipalities even if one fails
                
                if geocoded_count > 0:
                    conn.commit()
                    logger.info(f"Geocoded {geocoded_count} municipalities on-demand")
                
                cur.close()
                
                result = {
                    'municipalities': [dict(m) for m in municipalities],
                    'count': len(municipalities)
                }
                logger.info(f"Returning {len(municipalities)} municipalities for query '{query}'")
                return jsonify(result), 200
            
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Error searching municipalities: {e}", exc_info=True)
                
                # Check if it's a missing table error
                if 'does not exist' in error_msg.lower() or 'relation' in error_msg.lower():
                    logger.error(f"CRITICAL: Required table 'catalog_municipalities' does not exist. Database migrations may not have been applied.")
                    return jsonify({
                        'error': 'Database schema incomplete',
                        'detail': 'The catalog_municipalities table is missing. Please run database migrations (010_sensor_ingestion_schema.sql).',
                        'migration_file': 'config/timescaledb/migrations/010_sensor_ingestion_schema.sql'
                    }), 500
                
                return jsonify({'error': 'Database error', 'detail': error_msg}), 500
    
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in search_municipalities: {e}", exc_info=True)
        
        # Check if it's an authentication/tenant error
        if 'tenant' in error_msg.lower() or 'g.tenant' in error_msg:
            logger.error(f"CRITICAL: Tenant not set in request context. Authentication may have failed.")
            return jsonify({
                'error': 'Authentication error',
                'detail': 'Tenant information not available. Please check authentication configuration.'
            }), 500
        
        return jsonify({'error': 'Internal server error', 'detail': error_msg}), 500

# === Lines 2636-2679 from entity_management_api.py ===
@weather_bp.route('/api/weather/locations', methods=['GET'])
@require_auth(require_hmac=False)  # Public read-only endpoint, no HMAC required
def get_weather_locations():
    """Get weather locations configured for the tenant"""
    try:
        tenant_id = g.tenant
        
        with get_db_connection_with_tenant(tenant_id) as conn:
            if not conn:
                return jsonify({'error': 'Database connection error'}), 500
            
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute("""
                    SELECT 
                        twl.id,
                        twl.municipality_code,
                        cm.name as municipality_name,
                        cm.latitude,
                        cm.longitude,
                        twl.station_id,
                        twl.label,
                        twl.is_primary,
                        twl.metadata
                    FROM tenant_weather_locations twl
                    JOIN catalog_municipalities cm ON cm.ine_code = twl.municipality_code
                    WHERE twl.tenant_id = %s
                    ORDER BY twl.is_primary DESC, twl.created_at DESC
                """, (tenant_id,))
                
                locations = cur.fetchall()
                cur.close()
                
                return jsonify({
                    'locations': [dict(loc) for loc in locations]
                }), 200
            
            except Exception as e:
                logger.error(f"Error getting weather locations: {e}")
                return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error in get_weather_locations: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 2682-2745 from entity_management_api.py ===
@weather_bp.route('/api/weather/municipality/near', methods=['GET'])
@require_auth(require_hmac=False)  # Public read-only endpoint
def get_nearest_municipality():
    """
    Get nearest municipality to given coordinates.
    Useful for finding municipality from parcel centroid.
    """
    try:
        tenant_id = g.tenant
        latitude = request.args.get('latitude', type=float)
        longitude = request.args.get('longitude', type=float)
        max_distance_km = request.args.get('max_distance_km', type=float, default=50.0)
        
        if not latitude or not longitude:
            return jsonify({'error': 'latitude and longitude are required'}), 400
        
        with get_db_connection_with_tenant(tenant_id) as conn:
            if not conn:
                return jsonify({'error': 'Database connection error'}), 500
            
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                # Find nearest municipality using PostGIS ST_Distance
                cur.execute("""
                    SELECT 
                        ine_code,
                        name,
                        province,
                        autonomous_community,
                        latitude,
                        longitude,
                        ST_Distance(
                            geom::geography,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                        ) / 1000.0 as distance_km
                    FROM catalog_municipalities
                    WHERE geom IS NOT NULL
                    AND ST_Distance(
                        geom::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) / 1000.0 <= %s
                    ORDER BY distance_km ASC
                    LIMIT 1
                """, (longitude, latitude, longitude, latitude, max_distance_km))
                
                municipality = cur.fetchone()
                cur.close()
                
                if municipality:
                    return jsonify({
                        'municipality': dict(municipality)
                    }), 200
                else:
                    return jsonify({
                        'error': 'No municipality found within specified distance'
                    }), 404
            
            except Exception as e:
                logger.error(f"Error finding nearest municipality: {e}")
                return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error in get_nearest_municipality: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 2935-3081 from entity_management_api.py ===
@weather_bp.route('/api/weather/locations', methods=['POST'])
@require_auth(require_hmac=False)  # Public endpoint, no HMAC required
def create_weather_location():
    """Create a new weather location for the tenant"""
    try:
        tenant_id = g.tenant
        data = request.get_json()
        
        if not data or 'municipality_code' not in data:
            return jsonify({'error': 'municipality_code is required'}), 400
        
        municipality_code = data.get('municipality_code')
        is_primary = data.get('is_primary', False)
        label = data.get('label')
        station_id = data.get('station_id')
        metadata = data.get('metadata', {})
        
        with get_db_connection_with_tenant(tenant_id) as conn:
            if not conn:
                return jsonify({'error': 'Database connection error'}), 500
            
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                
                # Verify municipality exists in catalog, create if not exists
                cur.execute("""
                    SELECT ine_code, name FROM catalog_municipalities 
                    WHERE ine_code = %s
                """, (municipality_code,))
                municipality = cur.fetchone()
                
                if not municipality:
                    # Municipality not in catalog - create it with basic info
                    # Common municipalities mapping (INE codes)
                    common_municipalities = {
                        '31001': {'name': 'Pamplona', 'province': 'Navarra', 'latitude': 42.8169, 'longitude': -1.6432},
                        '28079': {'name': 'Madrid', 'province': 'Madrid', 'latitude': 40.4168, 'longitude': -3.7038},
                        '08019': {'name': 'Barcelona', 'province': 'Barcelona', 'latitude': 41.3851, 'longitude': 2.1734},
                        '41091': {'name': 'Sevilla', 'province': 'Sevilla', 'latitude': 37.3891, 'longitude': -5.9845},
                        '46015': {'name': 'Valencia', 'province': 'Valencia', 'latitude': 39.4699, 'longitude': -0.3763},
                        '15030': {'name': 'A Coruña', 'province': 'A Coruña', 'latitude': 43.3623, 'longitude': -8.4115},
                        '29067': {'name': 'Málaga', 'province': 'Málaga', 'latitude': 36.7213, 'longitude': -4.4214},
                        '33044': {'name': 'Oviedo', 'province': 'Asturias', 'latitude': 43.3619, 'longitude': -5.8494},
                        '48020': {'name': 'Bilbao', 'province': 'Vizcaya', 'latitude': 43.2627, 'longitude': -2.9253},
                        '50059': {'name': 'Zaragoza', 'province': 'Zaragoza', 'latitude': 41.6488, 'longitude': -0.8891},
                    }
                    
                    mun_data = common_municipalities.get(municipality_code)
                    if mun_data:
                        # Create municipality in catalog
                        cur.execute("""
                            INSERT INTO catalog_municipalities 
                            (ine_code, name, province, latitude, longitude, geom)
                            VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                            ON CONFLICT (ine_code) DO NOTHING
                        """, (
                            municipality_code,
                            mun_data['name'],
                            mun_data['province'],
                            mun_data['longitude'],
                            mun_data['latitude'],
                            mun_data['longitude'],
                            mun_data['latitude']
                        ))
                        logger.info(f"Created municipality {municipality_code} ({mun_data['name']}) in catalog")
                    else:
                        # Unknown municipality code - create with minimal info
                        cur.execute("""
                            INSERT INTO catalog_municipalities 
                            (ine_code, name, latitude, longitude, geom)
                            VALUES (%s, %s, NULL, NULL, NULL)
                            ON CONFLICT (ine_code) DO NOTHING
                        """, (municipality_code, f'Municipality {municipality_code}'))
                        logger.warning(f"Created municipality {municipality_code} with minimal info")
                    
                    # Re-fetch municipality
                    cur.execute("""
                        SELECT ine_code, name FROM catalog_municipalities 
                        WHERE ine_code = %s
                    """, (municipality_code,))
                    municipality = cur.fetchone()
                
                # If setting as primary, unset other primary locations
                if is_primary:
                    cur.execute("""
                        UPDATE tenant_weather_locations 
                        SET is_primary = false 
                        WHERE tenant_id = %s
                    """, (tenant_id,))
                
                # Insert new location
                cur.execute("""
                    INSERT INTO tenant_weather_locations 
                    (tenant_id, municipality_code, station_id, label, is_primary, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, municipality_code) 
                    DO UPDATE SET 
                        station_id = EXCLUDED.station_id,
                        label = EXCLUDED.label,
                        is_primary = EXCLUDED.is_primary,
                        metadata = EXCLUDED.metadata,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id, municipality_code, station_id, label, is_primary, metadata, created_at, updated_at
                """, (tenant_id, municipality_code, station_id, label, is_primary, json.dumps(metadata)))
                
                result = cur.fetchone()
                conn.commit()
                
                if not result:
                    logger.error(f"No result returned from INSERT for tenant {tenant_id}, municipality {municipality_code}")
                    return jsonify({'error': 'Failed to create location'}), 500
                
                # Get full location with municipality name
                cur.execute("""
                    SELECT 
                        twl.id,
                        twl.municipality_code,
                        cm.name as municipality_name,
                        cm.latitude,
                        cm.longitude,
                        twl.station_id,
                        twl.label,
                        twl.is_primary,
                        twl.metadata
                    FROM tenant_weather_locations twl
                    JOIN catalog_municipalities cm ON cm.ine_code = twl.municipality_code
                    WHERE twl.id = %s
                """, (result['id'],))
                
                location = cur.fetchone()
                cur.close()
                
                if not location:
                    logger.error(f"Location not found after creation: id={result['id']}")
                    return jsonify({'error': 'Location created but not found'}), 500
                
                return jsonify({
                    'location': dict(location)
                }), 201
            
            except Exception as e:
                logger.error(f"Error creating weather location: {e}")
                return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error in create_weather_location: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 3084-3155 from entity_management_api.py ===
@weather_bp.route('/api/weather/observations/latest', methods=['GET'])
@require_auth(require_hmac=False)  # Public read-only endpoint, no HMAC required
def get_latest_weather_observations():
    """Get latest weather observations for tenant locations"""
    try:
        tenant_id = g.tenant
        if not tenant_id:
            logger.warning("Tenant not provided in request context; falling back to 'default' tenant for public weather reads")
            tenant_id = 'default'
        municipality_code = request.args.get('municipality_code')
        source = request.args.get('source', 'OPEN-METEO')  # Default to Open-Meteo
        data_type = request.args.get('data_type', 'HISTORY')  # Default to history
        
        def _fetch_for_tenant(tid: str):
            with get_db_connection_with_tenant(tid) as conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                query = """
                    SELECT DISTINCT ON (municipality_code, source, data_type)
                        municipality_code,
                        source,
                        data_type,
                        observed_at,
                        temp_avg,
                        temp_min,
                        temp_max,
                        humidity_avg,
                        precip_mm,
                        solar_rad_w_m2,
                        solar_rad_ghi_w_m2,
                        solar_rad_dni_w_m2,
                        eto_mm,
                        soil_moisture_0_10cm,
                        soil_moisture_10_40cm,
                        wind_speed_ms,
                        wind_direction_deg,
                        pressure_hpa,
                        gdd_accumulated,
                        delta_t,
                        metrics,
                        metadata
                    FROM weather_observations
                    WHERE tenant_id = %s
                """
                params = [tid]
                if municipality_code:
                    query += " AND municipality_code = %s"
                    params.append(municipality_code)
                if source:
                    query += " AND source = %s"
                    params.append(source)
                if data_type:
                    query += " AND data_type = %s"
                    params.append(data_type)
                query += " ORDER BY municipality_code, source, data_type, observed_at DESC"
                cur.execute(query, params)
                rows = cur.fetchall()
                cur.close()
                return rows

        try:
            observations = _fetch_for_tenant(tenant_id)
            if not observations and tenant_id != 'default':
                logger.info(f"No observations for tenant {tenant_id}, falling back to default")
                observations = _fetch_for_tenant('default')
            return jsonify({'observations': [dict(obs) for obs in observations]}), 200
        except Exception as e:
            logger.error(f"Error getting latest weather observations: {e}")
            return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error in get_latest_weather_observations: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 3158-3260 from entity_management_api.py ===
@weather_bp.route('/api/weather/observations', methods=['GET'])
@require_auth(require_hmac=False)  # Public read-only endpoint, no HMAC required
def get_weather_observations():
    """Get weather observations with optional filters"""
    try:
        tenant_id = g.tenant
        if not tenant_id:
            logger.warning("Tenant not provided in request context; falling back to 'default' tenant for public weather reads")
            tenant_id = 'default'
        municipality_code = request.args.get('municipality_code')
        source = request.args.get('source')
        data_type = request.args.get('data_type')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        limit = int(request.args.get('limit', 100))
        # For FORECAST, ensure we return enough range for 5-day widget when data exists
        if data_type == 'FORECAST' and not start_date and not end_date:
            now = datetime.utcnow()
            start_date = (now - timedelta(hours=1)).isoformat()  # include near-future
            end_date = (now + timedelta(days=8)).isoformat()      # 8 days ahead
            limit = min(limit, 250) if limit <= 100 else limit     # default 250 for forecast
        if data_type == 'FORECAST' and limit == 100:
            limit = 250

        def _fetch_for_tenant(tid: str):
            with get_db_connection_with_tenant(tid) as conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                
                query = """
                    SELECT 
                        municipality_code,
                        source,
                        data_type,
                        observed_at,
                        temp_avg,
                        temp_min,
                        temp_max,
                        humidity_avg,
                        precip_mm,
                        solar_rad_w_m2,
                        solar_rad_ghi_w_m2,
                        solar_rad_dni_w_m2,
                        eto_mm,
                        soil_moisture_0_10cm,
                        soil_moisture_10_40cm,
                        wind_speed_ms,
                        wind_direction_deg,
                        pressure_hpa,
                        gdd_accumulated,
                        delta_t,
                        metrics,
                        metadata
                    FROM weather_observations
                    WHERE tenant_id = %s
                """
                params = [tid]
                
                if municipality_code:
                    query += " AND municipality_code = %s"
                    params.append(municipality_code)
                
                if source:
                    query += " AND source = %s"
                    params.append(source)
                
                if data_type:
                    query += " AND data_type = %s"
                    params.append(data_type)
                
                if start_date:
                    query += " AND observed_at >= %s"
                    params.append(start_date)
                
                if end_date:
                    query += " AND observed_at <= %s"
                    params.append(end_date)
                
                query += " ORDER BY observed_at DESC LIMIT %s"
                params.append(limit)
                
                cur.execute(query, params)
                rows = cur.fetchall()
                cur.close()
                return rows
        
        try:
            observations = _fetch_for_tenant(tenant_id)
            if not observations and tenant_id != 'default':
                logger.info(f"No observations for tenant {tenant_id}, falling back to default")
                observations = _fetch_for_tenant('default')
            
            return jsonify({
                'observations': [dict(obs) for obs in observations],
                'count': len(observations)
            }), 200
        
        except Exception as e:
            logger.error(f"Error getting weather observations: {e}")
            return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error in get_weather_observations: {e}")
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 3263-3480 from entity_management_api.py ===
@weather_bp.route('/api/weather/parcel/<parcel_id>', methods=['GET'])
@require_auth(require_hmac=False)
def get_parcel_weather(parcel_id):
    """
    Canonical weather endpoint for a specific parcel.

    Resolves the parcel's location from Orion-LD, finds the nearest
    weather observations, applies spatial downscaling (altitude, aspect,
    slope), and returns corrected data.

    This is the SINGLE SOURCE OF TRUTH for parcel-level weather.
    All consumers (risk-worker, crop-health, vegetation-prime, frontend)
    should use this endpoint instead of querying weather_observations directly.

    Query params:
        source: 'OPEN-METEO' (default), 'AEMET', 'SENSOR_REAL'
        data_type: 'HISTORY' (default), 'FORECAST'
        limit: number of observations (default 1, max 72)

    Response:
        parcel_id, municipality_code, downscaling, observations[]
    """
    tenant_id = getattr(g, 'tenant_id', None) or getattr(g, 'tenant', 'default')
    source = request.args.get('source', 'OPEN-METEO')
    data_type = request.args.get('data_type', 'HISTORY')
    limit = min(int(request.args.get('limit', '1')), 72)

    try:
        # Step 1: Resolve parcel location from Orion-LD
        orion_url = os.getenv('ORION_URL', 'http://orion-ld-service:1026')
        context_url = os.getenv('CONTEXT_URL', '')

        headers = {
            'Fiware-Service': tenant_id,
            'Fiware-ServicePath': '/',
            'Accept': 'application/ld+json',
        }
        if context_url:
            headers['Link'] = (
                f'<{context_url}>; rel="http://www.w3.org/ns/json-ld#context";'
                f' type="application/ld+json"'
            )

        parcel_resp = requests.get(
            f'{orion_url}/ngsi-ld/v1/entities/{parcel_id}',
            headers=headers,
            timeout=10,
        )
        if parcel_resp.status_code != 200:
            return jsonify({'error': f'Parcel not found: {parcel_resp.status_code}'}), 404

        parcel = parcel_resp.json()

        # Step 2: Extract parcel location coordinates
        location_attr = parcel.get('location', {})
        if isinstance(location_attr, dict):
            loc_value = location_attr.get('value', {})
        else:
            loc_value = location_attr

        parcel_lat, parcel_lon = None, None
        if isinstance(loc_value, dict):
            geom_type = loc_value.get('type', '')
            coords = loc_value.get('coordinates', [])
            if geom_type == 'Point' and len(coords) >= 2:
                parcel_lon, parcel_lat = float(coords[0]), float(coords[1])
            elif geom_type in ('Polygon', 'MultiPolygon') and coords:
                # Use first ring centroid approximation
                ring = coords[0] if geom_type == 'Polygon' else coords[0][0]
                xs = [p[0] for p in ring]
                ys = [p[1] for p in ring]
                parcel_lon = sum(xs) / len(xs)
                parcel_lat = sum(ys) / len(ys)

        if parcel_lat is None or parcel_lon is None:
            return jsonify({'error': 'Parcel has no resolvable location'}), 400

        # Step 3: Extract terrain attributes for downscaling
        parcel_altitude = 0.0
        elev = parcel.get('elevation', {})
        if isinstance(elev, dict):
            parcel_altitude = float(elev.get('value', 0) or 0)

        parcel_aspect = 0.0
        ta = parcel.get('terrainAspect', {})
        if isinstance(ta, dict):
            parcel_aspect = float(ta.get('value', 0) or 0)

        parcel_slope = 0.0
        ts = parcel.get('terrainSlope', {})
        if isinstance(ts, dict):
            parcel_slope = float(ts.get('value', 0) or 0)

        # Step 4: Find nearest municipality with weather data
        with get_db_connection_with_tenant(tenant_id) as conn:
            cur = conn.cursor(cursor_factory=RealDictCursor)

            cur.execute("""
                SELECT
                    cm.ine_code as municipality_code,
                    cm.name as municipality_name,
                    cm.latitude as station_lat,
                    cm.longitude as station_lon,
                    cm.latitude,  -- station altitude not in catalog yet
                    ST_Distance(
                        cm.geom,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                    ) as distance_m
                FROM catalog_municipalities cm
                WHERE cm.latitude IS NOT NULL
                  AND cm.longitude IS NOT NULL
                  AND cm.geom IS NOT NULL
                ORDER BY cm.geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
                LIMIT 1
            """, (parcel_lon, parcel_lat, parcel_lon, parcel_lat))
            municipality = cur.fetchone()

            if not municipality:
                return jsonify({'error': 'No municipality with weather data found near parcel'}), 404

            muni_code = municipality['municipality_code']
            station_lat = float(municipality['station_lat'])
            station_lon = float(municipality['station_lon'])

            # Step 5: Get weather observations for this municipality
            cur.execute("""
                SELECT
                    observed_at, temp_avg, temp_min, temp_max,
                    humidity_avg, precip_mm,
                    solar_rad_w_m2, solar_rad_ghi_w_m2, solar_rad_dni_w_m2,
                    eto_mm, soil_moisture_0_10cm, soil_moisture_10_40cm,
                    wind_speed_ms, wind_direction_deg, pressure_hpa,
                    gdd_accumulated, delta_t,
                    source, data_type, metadata
                FROM weather_observations
                WHERE tenant_id = %s
                  AND municipality_code = %s
                  AND source = %s
                  AND data_type = %s
                ORDER BY observed_at DESC
                LIMIT %s
            """, (tenant_id, muni_code, source, data_type, limit))
            observations = [dict(row) for row in cur.fetchall()]
            cur.close()

            # Extract station elevation from observation metadata (populated
            # by Open-Meteo provider). Falls back to 0.0 if unavailable.
            station_altitude = 0.0
            if observations:
                meta = observations[0].get('metadata') or {}
                if isinstance(meta, dict):
                    station_altitude = float(meta.get('station_elevation_m', 0) or 0)

        if not observations:
            return jsonify({
                'parcel_id': parcel_id,
                'municipality_code': muni_code,
                'municipality_name': municipality.get('municipality_name'),
                'downscaling': 'unavailable',
                'observations': [],
            }), 200

        # Step 6: Apply spatial downscaling to each observation
        downscaling_applied = False
        try:
            sys.path.insert(0, '/app/weather-worker')
            from weather_worker.processors.spatial_downscaler import (
                downscale_for_parcel,
                recalculate_delta_t,
                correct_temperature_altitude,
            )

            corrected_observations = []
            for obs in observations:
                obs_dt = obs.get('observed_at')
                doy = obs_dt.timetuple().tm_yday if hasattr(obs_dt, 'timetuple') else None

                corrected = downscale_for_parcel(
                    weather_data=obs,
                    parcel_lat=parcel_lat,
                    parcel_lon=parcel_lon,
                    parcel_altitude_m=parcel_altitude,
                    station_altitude_m=station_altitude,
                    parcel_aspect_deg=parcel_aspect,
                    parcel_slope_deg=parcel_slope,
                    doy=doy,
                )
                # Merge in metadata fields preserved from original
                for key in ('observed_at', 'source', 'data_type', 'municipality_code'):
                    if key in obs:
                        corrected[key] = obs[key]
                corrected_observations.append(corrected)

            observations = corrected_observations
            downscaling_applied = (parcel_altitude > 0 or parcel_slope >= 1.0)

        except ImportError:
            logger.debug('Spatial downscaler not available — returning raw observations')
        except Exception as exc:
            logger.warning(f'Downscaling error (returning raw data): {exc}')

        return jsonify({
            'parcel_id': parcel_id,
            'municipality_code': muni_code,
            'municipality_name': municipality.get('municipality_name'),
            'parcel_altitude_m': parcel_altitude,
            'station_altitude_m': station_altitude,
            'parcel_aspect_deg': parcel_aspect,
            'parcel_slope_deg': parcel_slope,
            'downscaling': 'applied' if downscaling_applied else 'unavailable',
            'observations': observations,
        }), 200

    except requests.exceptions.Timeout:
        return jsonify({'error': 'Orion-LD request timed out'}), 504
    except Exception as e:
        logger.error(f'Error in get_parcel_weather: {e}', exc_info=True)
        return jsonify({'error': 'Failed to fetch parcel weather'}), 500

# === Lines 3483-3834 from entity_management_api.py ===
@weather_bp.route('/api/weather/parcel/<parcel_id>/agro-status', methods=['GET'])
@require_auth
def get_parcel_agro_status(parcel_id):
    """
    Get agronomic weather status for a parcel.

    Fuses sensor data (if available) with Open-Meteo data:
    - Priority: Sensor > Open-Meteo
    - Calculates parcel centroid from geometry
    - Returns current conditions and agroclimatic metrics
    """
    try:
        tenant_id = g.tenant
        
        # Import geo_utils here to avoid circular imports
        from geo_utils import get_parcel_location
        
        # 1. Get parcel from Orion-LD
        orion_url = f"{ORION_URL}/ngsi-ld/v1/entities/{parcel_id}"
        headers = {
            'Accept': 'application/ld+json'
        }
        headers = inject_fiware_headers(headers, tenant_id)
        
        response = requests.get(orion_url, headers=headers, timeout=10)
        if response.status_code == 404:
            return jsonify({'error': 'Parcel not found'}), 404
        elif response.status_code != 200:
            logger.error(f"Error fetching parcel from Orion: {response.status_code}")
            return jsonify({'error': 'Failed to fetch parcel'}), 500
        
        parcel_entity = response.json()
        
        # 2. Calculate centroid from parcel geometry
        location = get_parcel_location(parcel_entity)
        if not location:
            # Try to get location from municipality if parcel has one
            municipality = parcel_entity.get('municipality', {}).get('value') if isinstance(parcel_entity.get('municipality'), dict) else parcel_entity.get('municipality')
            if municipality:
                # Try to find municipality coordinates from catalog
                try:
                    conn = get_db_connection_with_tenant(tenant_id)
                    if conn:
                        cur = conn.cursor(cursor_factory=RealDictCursor)
                        cur.execute("""
                            SELECT latitude, longitude 
                            FROM catalog_municipalities 
                            WHERE name ILIKE %s OR ine_code = %s
                            LIMIT 1
                        """, (f"%{municipality}%", municipality))
                        mun_row = cur.fetchone()
                        cur.close()
                        conn.close()
                        
                        if mun_row and mun_row.get('latitude') and mun_row.get('longitude'):
                            lat = float(mun_row['latitude'])
                            lon = float(mun_row['longitude'])
                            logger.info(f"Using municipality coordinates for parcel {parcel_id}: {lat}, {lon}")
                        else:
                            return jsonify({
                                'error': 'Parcel has no valid location/geometry',
                                'details': 'Parcel location could not be determined from geometry or municipality'
                            }), 400
                    else:
                        return jsonify({
                            'error': 'Parcel has no valid location/geometry',
                            'details': 'Database connection failed'
                        }), 400
                except Exception as e:
                    logger.warning(f"Error trying municipality fallback: {e}")
                    return jsonify({
                        'error': 'Parcel has no valid location/geometry',
                        'details': str(e)
                    }), 400
            else:
                return jsonify({
                    'error': 'Parcel has no valid location/geometry',
                    'details': 'Parcel has no location attribute and no municipality information'
                }), 400
        else:
            lon, lat = location
        
        # 3. Try to get sensor data near the parcel (within 5km radius)
        sensor_data = None
        try:
            conn = get_db_connection_with_tenant(tenant_id)
            if conn:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                # Find sensors within 5km of parcel centroid
                cur.execute("""
                    SELECT 
                        s.external_id,
                        s.name,
                        ST_X(s.installation_location::geometry) as lon,
                        ST_Y(s.installation_location::geometry) as lat,
                        ST_Distance(
                            s.installation_location::geography,
                            ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                        ) as distance_m,
                        te.observed_at,
                        te.payload
                    FROM sensors s
                    LEFT JOIN LATERAL (
                        SELECT observed_at, payload
                        FROM telemetry_events
                        WHERE tenant_id = %s 
                        AND device_id = s.external_id
                        ORDER BY observed_at DESC
                        LIMIT 1
                    ) te ON true
                    WHERE s.tenant_id = %s
                    AND ST_Distance(
                        s.installation_location::geography,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
                    ) <= 5000
                    ORDER BY distance_m ASC
                    LIMIT 1
                """, (lon, lat, tenant_id, tenant_id, lon, lat))
                
                sensor_row = cur.fetchone()
                cur.close()
                conn.close()
                
                if sensor_row and sensor_row['payload']:
                    sensor_data = {
                        'external_id': sensor_row['external_id'],
                        'name': sensor_row['name'],
                        'distance_m': float(sensor_row['distance_m']) if sensor_row['distance_m'] else None,
                        'observed_at': sensor_row['observed_at'].isoformat() if sensor_row['observed_at'] else None,
                        'payload': sensor_row['payload'] if isinstance(sensor_row['payload'], dict) else json.loads(sensor_row['payload']) if sensor_row['payload'] else {}
                    }
        except Exception as e:
            logger.warning(f"Error fetching sensor data: {e}")
            # Continue without sensor data
        
        # 4. Fetch Open-Meteo data for centroid
        openmeteo_data = None
        try:
            openmeteo_url = "https://api.open-meteo.com/v1/forecast"
            params = {
                'latitude': lat,
                'longitude': lon,
                'current': 'temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,pressure_msl,precipitation',
                'hourly': 'temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m',
                'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,et0_fao_evapotranspiration',
                'timezone': 'Europe/Madrid',
                'forecast_days': 7
            }
            
            response = requests.get(openmeteo_url, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                current = data.get('current', {})
                daily = data.get('daily', {})
                
                openmeteo_data = {
                    'temperature': current.get('temperature_2m'),
                    'humidity': current.get('relative_humidity_2m'),
                    'wind_speed': current.get('wind_speed_10m'),
                    'wind_direction': current.get('wind_direction_10m'),
                    'pressure': current.get('pressure_msl'),
                    'precipitation': current.get('precipitation', 0),
                    'et0_today': daily.get('et0_fao_evapotranspiration', [0])[0] if daily.get('et0_fao_evapotranspiration') else None,
                    'precipitation_3d': sum(daily.get('precipitation_sum', [0])[:3]) if daily.get('precipitation_sum') else 0,
                    'et0_3d': sum(daily.get('et0_fao_evapotranspiration', [0])[:3]) if daily.get('et0_fao_evapotranspiration') else None,
                    'observed_at': datetime.utcnow().isoformat() + 'Z'
                }
        except Exception as e:
            logger.error(f"Error fetching Open-Meteo data: {e}")
            return jsonify({'error': 'Failed to fetch weather data'}), 500
        
        if not openmeteo_data:
            return jsonify({'error': 'No weather data available'}), 503
        
        # 5. Fuse sensor and Open-Meteo data (Sensor > Open-Meteo priority)
        fused = {
            'temperature': openmeteo_data.get('temperature'),
            'humidity': openmeteo_data.get('humidity'),
            'wind_speed': openmeteo_data.get('wind_speed'),
            'wind_direction': openmeteo_data.get('wind_direction'),
            'pressure': openmeteo_data.get('pressure'),
            'precipitation': openmeteo_data.get('precipitation', 0),
            'precipitation_3d': openmeteo_data.get('precipitation_3d', 0),
            'et0_today': openmeteo_data.get('et0_today'),
            'et0_3d': openmeteo_data.get('et0_3d'),
            'sources': {
                'temperature': 'OPEN-METEO',
                'humidity': 'OPEN-METEO',
                'wind_speed': 'OPEN-METEO',
                'wind_direction': 'OPEN-METEO',
                'pressure': 'OPEN-METEO',
                'precipitation': 'OPEN-METEO'
            },
            'source_confidence': 'OPEN-METEO'
        }
        
        # Override with sensor data if available
        if sensor_data and sensor_data.get('payload'):
            payload = sensor_data['payload']
            # Map sensor payload to weather metrics (adjust based on your sensor schema)
            if 'temperature' in payload or 'temp' in payload:
                fused['temperature'] = payload.get('temperature') or payload.get('temp')
                fused['sources']['temperature'] = 'SENSOR_REAL'
            if 'humidity' in payload:
                fused['humidity'] = payload.get('humidity')
                fused['sources']['humidity'] = 'SENSOR_REAL'
            if 'wind_speed' in payload:
                fused['wind_speed'] = payload.get('wind_speed')
                fused['sources']['wind_speed'] = 'SENSOR_REAL'
            if 'wind_direction' in payload:
                fused['wind_direction'] = payload.get('wind_direction')
                fused['sources']['wind_direction'] = 'SENSOR_REAL'
            if 'pressure' in payload:
                fused['pressure'] = payload.get('pressure')
                fused['sources']['pressure'] = 'SENSOR_REAL'
            
            fused['source_confidence'] = 'SENSOR_REAL'
            fused['sensor'] = {
                'external_id': sensor_data['external_id'],
                'name': sensor_data['name'],
                'distance_m': sensor_data['distance_m'],
                'last_observation': sensor_data['observed_at']
            }
        
        # 6. Calculate water balance (precipitation - ET0)
        if fused.get('precipitation_3d') is not None and fused.get('et0_3d') is not None:
            fused['water_balance'] = fused['precipitation_3d'] - fused['et0_3d']
        
        # 7. Calculate Delta T (wet-bulb depression) for spraying semaphore
        delta_t = None
        if fused.get('temperature') is not None and fused.get('humidity') is not None:
            try:
                import math
                # Calculate dew point (Magnus formula)
                a = 17.27
                b = 237.7
                temp = fused['temperature']
                hum = fused['humidity']
                alpha = ((a * temp) / (b + temp)) + math.log(hum / 100.0)
                dew_point = (b * alpha) / (a - alpha)
                # Approximate wet bulb temperature
                wet_bulb = temp - (temp - dew_point) * 0.4
                # Delta T = T_dry - T_wet
                delta_t = round(temp - wet_bulb, 2)
            except Exception as e:
                logger.warning(f"Error calculating Delta T: {e}")
        
        # 8. Calculate agronomic semaphores
        semaphores = {
            'spraying': 'unknown',
            'workability': 'unknown',
            'irrigation': 'unknown'
        }
        
        # Spraying semaphore (based on Delta T and wind speed)
        wind_speed_ms = fused.get('wind_speed', 0)
        wind_speed_kmh = wind_speed_ms * 3.6 if wind_speed_ms else 0
        precip = fused.get('precipitation', 0)
        
        if delta_t is not None and wind_speed_kmh is not None:
            # Green: Wind < 15km/h AND Delta T 2-8
            if wind_speed_kmh < 15 and 2 <= delta_t <= 8:
                semaphores['spraying'] = 'optimal'
            # Red: Wind > 20km/h OR Delta T > 10 OR Precip > 0.5mm
            elif wind_speed_kmh > 20 or delta_t > 10 or (precip and precip > 0.5):
                semaphores['spraying'] = 'not_suitable'
            # Yellow: Otherwise
            else:
                semaphores['spraying'] = 'caution'
        
        # Workability semaphore (based on soil moisture)
        # Try to get soil moisture from sensor first, then Open-Meteo
        soil_moisture = None
        if sensor_data and sensor_data.get('payload'):
            payload = sensor_data['payload']
            # Check for soil moisture in sensor payload
            if 'soil_moisture' in payload:
                soil_moisture = payload.get('soil_moisture')
            elif 'moisture' in payload:
                soil_moisture = payload.get('moisture')
        
        # Fallback to Open-Meteo soil moisture if no sensor data
        if soil_moisture is None:
            # Open-Meteo provides soil_moisture_0_10cm in daily data
            # For now, we'll use a simple heuristic based on recent precipitation and humidity
            # In a more complete implementation, we'd fetch soil_moisture from Open-Meteo daily data
            # For workability, we can estimate from precipitation and humidity patterns
            recent_precip = fused.get('precipitation_3d', 0)
            humidity = fused.get('humidity', 0)
            
            # Simple heuristic: if high humidity and recent rain, soil is likely wet
            # If low humidity and no recent rain, soil is likely dry
            if recent_precip > 5 or humidity > 80:
                # Soil likely too wet
                semaphores['workability'] = 'too_wet'
            elif recent_precip == 0 and humidity < 40:
                # Soil likely too dry
                semaphores['workability'] = 'too_dry'
            elif 1 <= recent_precip <= 5 and 40 <= humidity <= 80:
                # Soil likely in good condition (tempero)
                semaphores['workability'] = 'optimal'
            else:
                # Borderline conditions
                semaphores['workability'] = 'caution'
        else:
            # Use actual sensor soil moisture
            if 15 <= soil_moisture <= 25:
                semaphores['workability'] = 'optimal'
            elif soil_moisture > 25:
                semaphores['workability'] = 'too_wet'
            elif soil_moisture < 10:
                semaphores['workability'] = 'too_dry'
            else:
                semaphores['workability'] = 'caution'
        
        # Irrigation semaphore (based on water balance)
        water_balance = fused.get('water_balance')
        if water_balance is not None:
            # Green: Balance > 0 (surplus)
            if water_balance > 0:
                semaphores['irrigation'] = 'satisfied'
            # Red: Balance < -5mm (deficit)
            elif water_balance < -5:
                semaphores['irrigation'] = 'deficit'
            # Yellow: Balance 0 to -5mm (alert)
            else:
                semaphores['irrigation'] = 'alert'
        
        # 9. Return agronomic status with semaphores
        return jsonify({
            'parcel_id': parcel_id,
            'parcel_name': parcel_entity.get('name', {}).get('value', 'Unnamed'),
            'centroid': {
                'latitude': lat,
                'longitude': lon
            },
            'weather': fused,
            'semaphores': semaphores,
            'metrics': {
                'temperature': fused.get('temperature'),
                'humidity': fused.get('humidity'),
                'delta_t': delta_t,
                'water_balance': fused.get('water_balance'),
                'wind_speed': fused.get('wind_speed')  # Add wind speed for tooltips
            },
            'source_confidence': fused.get('source_confidence', 'OPEN-METEO'),
            'timestamp': datetime.utcnow().isoformat() + 'Z'
        }), 200
    
    except Exception as e:
        logger.error(f"Error in get_parcel_agro_status: {e}", exc_info=True)
        return jsonify({'error': 'Internal server error'}), 500

# === Lines 3837-3904 from entity_management_api.py ===
@weather_bp.route('/api/weather/alerts', methods=['GET'])
@require_auth
def get_weather_alerts():
    """Get active weather alerts for tenant locations"""
    try:
        tenant_id = g.tenant
        municipality_code = request.args.get('municipality_code')
        alert_type = request.args.get('alert_type')  # YELLOW, ORANGE, RED
        active_only = request.args.get('active_only', 'true').lower() == 'true'
        
        conn = get_db_connection_with_tenant(tenant_id)
        if not conn:
            return jsonify({'error': 'Database connection error'}), 500
        
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            
            query = """
                SELECT 
                    id,
                    municipality_code,
                    alert_type,
                    alert_category,
                    effective_from,
                    effective_to,
                    description,
                    aemet_alert_id,
                    metadata
                FROM weather_alerts
                WHERE tenant_id = %s
            """
            params = [tenant_id]
            
            if municipality_code:
                query += " AND municipality_code = %s"
                params.append(municipality_code)
            
            if alert_type:
                query += " AND alert_type = %s"
                params.append(alert_type)
            
            if active_only:
                query += " AND effective_to >= CURRENT_TIMESTAMP"
            
            query += " ORDER BY effective_from DESC, alert_type DESC"
            
            cur.execute(query, params)
            alerts = cur.fetchall()
            cur.close()
            conn.close()
            
            return jsonify({
                'alerts': [dict(alert) for alert in alerts],
                'count': len(alerts)
            }), 200
        
        except Exception as e:
            conn.close()
            # Table may not exist if weather-worker hasn't run yet
            if 'relation "weather_alerts" does not exist' in str(e):
                logger.info("weather_alerts table does not exist yet, returning empty alerts")
                return jsonify({'alerts': [], 'count': 0}), 200
            logger.error(f"Error getting weather alerts: {e}")
            return jsonify({'error': 'Database error'}), 500
    
    except Exception as e:
        logger.error(f"Error in get_weather_alerts: {e}")
        return jsonify({'error': 'Internal server error'}), 500

