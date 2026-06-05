// =============================================================================
// useTenantHomeLocation Hook
// =============================================================================
// Hook to get tenant's home location (replaces useTenantMunicipality).
//
// Resolution priority:
// 1. tenant_weather_locations with is_primary=true (coordinates or municipality)
// 2. First parcel's centroid (from Orion-LD AgriParcel geometry)
// 3. null → UI shows "Configure your location"
//
// International-ready: no dependency on catalog_municipalities.
// =============================================================================

import { useState, useEffect } from 'react';
import api from '@/services/api';
import { parcelApi } from '@/services/parcelApi';
import { useAuth } from '@/context/KeycloakAuthContext';
import { logger } from '@/utils/logger';

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface HomeLocation {
  lat: number;
  lon: number;
  name: string;
  municipalityCode?: string;  // only for legacy Spain tenants
}

export const useTenantHomeLocation = () => {
  const [location, setLocation] = useState<HomeLocation | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { tenantId } = useAuth();

  useEffect(() => {
    const loadHomeLocation = async () => {
      setLoading(true);
      setError(null);

      try {
        // 1. Try primary weather location (coordinates-based or legacy municipality)
        try {
          const locations = await api.getWeatherLocations();
          const primary = locations.find((loc: any) => loc.is_primary === true);

          if (primary) {
            const lat = primary.latitude;
            const lon = primary.longitude;
            if (lat != null && lon != null) {
              setLocation({
                lat: Number(lat),
                lon: Number(lon),
                name: primary.location_name || primary.municipality_name || primary.label || 'Home',
                municipalityCode: primary.municipality_code || undefined,
              });
              setLoading(false);
              return;
            }
          }
        } catch (err) {
          logger.warn('[useTenantHomeLocation] Error loading weather locations:', err);
        }

        // 2. Fallback: first parcel's centroid
        try {
          const parcels = await parcelApi.getParcels();
          if (parcels.length > 0) {
            const firstParcel = parcels[0];

            // Extract centroid from geometry
            const centroid = extractCentroid(firstParcel);
            if (centroid) {
              const parcelName =
                (firstParcel.name?.value) ||
                firstParcel.name ||
                firstParcel.id?.split(':').pop() ||
                'Parcel';

              setLocation({
                lat: centroid.lat,
                lon: centroid.lon,
                name: String(parcelName),
              });
              setLoading(false);
              return;
            }
          }
        } catch (err) {
          logger.warn('[useTenantHomeLocation] Error loading parcels:', err);
        }

        // 3. No location found
        setLocation(null);
        setLoading(false);
      } catch (err: any) {
        logger.error('[useTenantHomeLocation] Error:', err);
        setError(err.message || 'Error loading home location');
        setLoading(false);
      }
    };

    loadHomeLocation();
  }, []);

  return { location, loading, error };
};

/**
 * Extract centroid (lat, lon) from a parcel entity's geometry.
 * Handles Point and Polygon GeoJSON from Orion-LD AgriParcel.
 */
function extractCentroid(parcel: any): { lat: number; lon: number } | null {
  // Try location attribute (NGSI-LD GeoProperty)
  const locAttr = parcel.location;
  if (!locAttr) return null;

  const locValue = locAttr.value || locAttr;
  if (!locValue) return null;

  const geomType = locValue.type;
  const coords = locValue.coordinates;

  if (geomType === 'Point' && Array.isArray(coords) && coords.length >= 2) {
    return { lon: Number(coords[0]), lat: Number(coords[1]) };
  }

  if ((geomType === 'Polygon' || geomType === 'MultiPolygon') && coords) {
    try {
      // Polygon: coordinates[0] is the outer ring
      // MultiPolygon: coordinates[0][0] is the first polygon's outer ring
      const ring =
        geomType === 'Polygon' ? coords[0] : coords[0]?.[0];
      if (!ring || ring.length === 0) return null;

      // Simple centroid: average of all ring points
      let sumLon = 0, sumLat = 0;
      for (const point of ring) {
        if (Array.isArray(point) && point.length >= 2) {
          sumLon += Number(point[0]);
          sumLat += Number(point[1]);
        }
      }
      return {
        lon: sumLon / ring.length,
        lat: sumLat / ring.length,
      };
    } catch {
      return null;
    }
  }

  return null;
}
