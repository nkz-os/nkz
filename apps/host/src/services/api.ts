// =============================================================================
// API Service - HTTP Client for Nekazari Backend
// =============================================================================

import axios, { AxiosInstance, AxiosError, AxiosRequestConfig } from 'axios';
import type {
/* eslint-disable @typescript-eslint/no-explicit-any */
  Robot,
  Sensor,
  Parcel,
  AgriculturalMachine,
  LivestockAnimal,
  WeatherStation,
  TenantUsageSummary,
  GrafanaLink,
} from '@/types';
import { getConfig } from '@/config/environment';
import { logger } from '@/utils/logger';
import { notifySessionExpired } from './sessionExpiry';
import { fetchAllPages } from './ngsiPaging';
import { pickParentRelationships } from '@/utils/entityRelations';

const config = getConfig();
const API_BASE_URL = config.api.baseUrl;

/** Tenant user as returned by /api/tenant/users */
export interface TenantUser {
  id: string;
  email: string;
  firstName?: string;
  lastName?: string;
  roles?: string[];
  username?: string;
  enabled?: boolean;
}

/** Response shape for /api/tenant/users (list) */
export interface TenantUsersResponse {
  users?: TenantUser[];
  [key: string]: unknown;
}

/** Response shape for /api/weather/parcel/:id/agro-status */
export interface AgroStatusResponse {
  parcel_id: string;
  parcel_name: string;
  centroid: { latitude: number; longitude: number };
  semaphores: {
    spraying: 'optimal' | 'caution' | 'not_suitable' | 'unknown';
    workability: 'optimal' | 'too_wet' | 'too_dry' | 'caution' | 'unknown';
    irrigation: 'satisfied' | 'alert' | 'deficit' | 'unknown';
  };
  weather: {
    temperature: number | null;
    humidity: number | null;
    wind_speed: number | null;
    wind_direction: number | null;
    pressure: number | null;
    precipitation: number;
    precipitation_3d: number | null;
    eto_today: number | null;
    eto_3d: number | null;
    wind_gusts: number | null;
    water_balance: number | null;
    observed_at: string | null;
    sources: Record<string, string>;
    source_confidence: string;
    sensor?: {
      external_id: string;
      name: string;
      distance_m: number;
      last_observation: string;
    };
  };
  metrics?: {
    temperature: number | null;
    humidity: number | null;
    delta_t: number | null;
    water_balance: number | null;
    wind_speed: number | null;
    wind_gusts: number | null;
    precip_probability: number | null;
    spraying_reason: string | null;
    moisture: number | null;
    soil_moisture_provenance?: 'iot_sensor' | 'parcel_weather' | null;
    // Tempero: limits published by the soil module; `regional_estimate` when the
    // moisture is the virtual station's (reanalysis), not a sensor in the parcel.
    workability_source?: 'iot_sensor' | 'regional_estimate' | null;
    workability_reason?: 'no_soil_moisture' | 'soil_thresholds_missing' | 'invalid_thresholds' | null;
    wet_tillage_limit?: number | null;
    dry_tillage_limit?: number | null;
  };
  soil?: {
    texture_applied: boolean;
    texture_class?: string;
    field_capacity?: number;
    wilting_point?: number;
    ksat?: number;
    hydrologic_group?: string;
    source?: string;
  };
  crop?: {
    stage?: string;
    spraying_sensitivity?: string;
  };
  inversion_risk: boolean;
  source_confidence: string;
  downscaling: string;
  timestamp: string;
}

// Internal Keycloak instance reference for token refresh / cookie update.
// Set via setKeycloakRef() from KeycloakAuthContext — never exposed to modules.
let _keycloakRef: { token?: string; updateToken: (minValidity: number) => Promise<boolean> } | null = null;

/** Called by KeycloakAuthContext to share the Keycloak instance with api.ts */
export const setKeycloakRef = (kc: typeof _keycloakRef) => { _keycloakRef = kc; };

// Internal helper — token is used only for refresh logic and X-Tenant-ID extraction,
// never sent as Authorization header (httpOnly cookie handles auth).
const getAuthToken = (): string | null => {
  return _keycloakRef?.token ?? null;
};

// =============================================================================
// Tenant ID cache — avoids decoding the JWT on every single request.
// =============================================================================
// The tenant ID is stable for the lifetime of a token. Decoding the JWT
// (split + atob + JSON.parse + property scan) on every request is wasteful.
// We cache the extracted tenant ID keyed by the token string, so a new
// token (after refresh) triggers a fresh extraction automatically.

let _cachedTenantToken: string | null = null;
let _cachedTenantId: string | null = null;

function _invalidateTenantCache(): void {
  _cachedTenantToken = null;
  _cachedTenantId = null;
}

/**
 * Extract tenant ID from the current Bearer token, using cache when possible.
 * Returns null if no token is available or tenant cannot be extracted.
 */
function _extractTenantId(): string | null {
  const token = getAuthToken();
  if (!token) return null;

  // Return cached value if token hasn't changed
  if (_cachedTenantToken === token && _cachedTenantId !== null) {
    return _cachedTenantId;
  }

  try {
    const decoded = JSON.parse(atob(token.split('.')[1]));
    const tenantId = decoded['tenant-id'] || decoded.tenant_id || decoded.tenantId || decoded.tenant || '';
    if (tenantId) {
      _cachedTenantToken = token;
      _cachedTenantId = tenantId;
      return tenantId;
    }
  } catch {
    logger.warn('[API] Could not extract tenant from token for X-Tenant-ID header');
  }
  return null;
}

// =============================================================================
// Simple in-memory cache for API responses
// =============================================================================

interface CacheEntry<T = unknown> {
  data: T;
  timestamp: number;
  ttl: number;
}

class SimpleCache {
  private cache = new Map<string, CacheEntry>();
  private readonly defaultTTL = 60000;

  get<T = unknown>(key: string): T | null {
    const entry = this.cache.get(key);
    if (!entry) return null;
    if (Date.now() - entry.timestamp > entry.ttl) {
      this.cache.delete(key);
      return null;
    }
    return entry.data as T;
  }

  set<T = unknown>(key: string, data: T, ttl: number = this.defaultTTL): void {
    this.cache.set(key, { data, timestamp: Date.now(), ttl });
  }

  clear(): void {
    this.cache.clear();
  }

  delete(key: string): void {
    this.cache.delete(key);
  }
}

// =============================================================================
// Retry utility
// =============================================================================

async function retryRequest<T>(
  fn: () => Promise<T>,
  maxRetries: number = 3,
  delay: number = 1000
): Promise<T> {
  let lastError: unknown;

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn();
    } catch (error: unknown) {
      lastError = error;
      const err = error as { response?: { status?: number } };

      if (err.response && err.response.status !== null && err.response.status !== undefined && err.response.status >= 400 && err.response.status < 500) {
        throw error;
      }

      // Don't retry on last attempt
      if (attempt === maxRetries) {
        break;
      }

      // Exponential backoff
      const waitTime = delay * Math.pow(2, attempt);
      await new Promise(resolve => setTimeout(resolve, waitTime));
    }
  }

  throw lastError;
}

// =============================================================================
// Alert (módulo risk federado) — entidad Alert leída de Orion-LD vía /api/risk/alerts
// =============================================================================

export interface AlertEntity {
  id: string;
  type: string;
  alertType?: string;
  category?: string;
  severity?: string;
  refEntity?: string;
  refEntityType?: string;
  probabilityScore?: number;
  observedAt?: string;
  status?: string;
}

function alertAttrValue(e: unknown, name: string): unknown {
  const a = (e as Record<string, any>)?.[name];
  if (a && typeof a === 'object') {
    if ('value' in a) return a.value;
    if ('object' in a) return a.object;
  }
  return a;
}

function normalizeAlert(e: unknown): AlertEntity {
  const rec = (e ?? {}) as Record<string, any>;
  return {
    id: rec.id ?? '',
    type: rec.type ?? 'Alert',
    alertType: alertAttrValue(e, 'alertType') as string | undefined,
    category: alertAttrValue(e, 'category') as string | undefined,
    severity: alertAttrValue(e, 'severity') as string | undefined,
    refEntity: alertAttrValue(e, 'refEntity') as string | undefined,
    refEntityType: alertAttrValue(e, 'refEntityType') as string | undefined,
    probabilityScore: alertAttrValue(e, 'probabilityScore') as number | undefined,
    observedAt: (alertAttrValue(e, 'observedAt') ?? rec.observedAt) as string | undefined,
    status: alertAttrValue(e, 'status') as string | undefined,
  };
}

class ApiService {
  private client: AxiosInstance;
  private cache: SimpleCache;

  constructor() {
    this.cache = new SimpleCache();
    this.client = axios.create({
      baseURL: API_BASE_URL,
      timeout: config.api.timeout,
      withCredentials: true,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Request interceptor — auth is handled by httpOnly cookie (withCredentials: true).
    // We still proactively refresh the token so the cookie stays fresh, and extract
    // X-Tenant-ID from the in-memory token for legacy services.
    this.client.interceptors.request.use(
      async (requestConfig) => {
        // Proactively refresh token if close to expiry (updates httpOnly cookie)
        if (_keycloakRef?.token) {
          try {
            const decoded = JSON.parse(atob(_keycloakRef.token.split('.')[1]));
            const timeUntilExpiry = decoded.exp * 1000 - Date.now();
            if (timeUntilExpiry < 60000) {
              logger.debug('[API] Token expiring soon, refreshing for cookie update...');
              try {
                await _keycloakRef.updateToken(60);
              } catch (e) {
                logger.warn('[API] Token refresh failed:', e);
              }
            }
          } catch (e) {
            logger.warn('[API] Error checking token expiry:', e);
          }
        }

        // Send Bearer token when available so auth works even if cookie is missing (e.g. cross-origin, timing).
        // Gateway accepts Authorization first, then nkz_token cookie.
        const token = getAuthToken();
        if (token) {
          requestConfig.headers['Authorization'] = `Bearer ${token}`;
          const tenantId = _extractTenantId();
          if (tenantId) {
            requestConfig.headers['X-Tenant-ID'] = tenantId;
          }
        }

        return requestConfig;
      },
      (error) => Promise.reject(error)
    );

    // Response interceptor for error handling and token refresh
    this.client.interceptors.response.use(
      (response) => {
        // Log successful responses for debugging
        return response;
      },
      async (error: AxiosError) => {
        // Handle empty responses or non-JSON responses
        if (error.response && typeof error.response.data === 'string' && error.response.data.trim() === '') {
          logger.warn('[API] Empty response received, converting to error object');
          error.response.data = { error: 'Empty response from server' };
        }

        // Handle JSON parse errors
        if (error.message && error.message.includes('JSON')) {
          logger.warn('[API] JSON parse error:', error.message);
          if (error.response) {
            try {
              // Try to parse the response as text first
              const text = typeof error.response.data === 'string'
                ? error.response.data
                : JSON.stringify(error.response.data);
              error.response.data = { error: text || 'Invalid response format' };
            } catch (e) {
              error.response.data = { error: 'Failed to parse server response' };
            }
          }
        }

        if (error.response?.status === 401) {
          // Prevent infinite refresh loops
          const retryCount = (error.config as any)?._retryCount || 0;
          const MAX_RETRY_ATTEMPTS = 2;

          if (retryCount >= MAX_RETRY_ATTEMPTS) {
            logger.error('[API] Max retry attempts reached for 401.', { url: error.config?.url });
            return Promise.reject(error);
          }

          // Try to refresh token → updates httpOnly cookie, then retry
          if (_keycloakRef) {
            try {
              logger.debug(`[API] 401 — refreshing token (attempt ${retryCount + 1}/${MAX_RETRY_ATTEMPTS})`);
              await _keycloakRef.updateToken(30);
              if (_keycloakRef.token) {
                // Clear tenant cache — token may have changed after refresh
                _invalidateTenantCache();
                // Update cookie with fresh token
                this.setSession(_keycloakRef.token).catch(() => {});
                logger.debug('[API] Token refreshed, retrying with updated cookie...');
                const originalRequest = error.config;
                if (originalRequest) {
                  (originalRequest as AxiosRequestConfig & { _retryCount?: number })._retryCount = retryCount + 1;
                  // No Bearer header — cookie is sent automatically via withCredentials
                  delete originalRequest.transformRequest;
                  delete originalRequest.transformResponse;
                  return this.client.request(originalRequest);
                }
              }
              // Reached only when the refresh left no usable token.
              notifySessionExpired('api.ts');
            } catch (refreshError: unknown) {
              logger.warn('[API] Token refresh failed on 401:', refreshError);
              notifySessionExpired('api.ts');
            }
          } else {
            logger.warn('[API] 401 error but no Keycloak ref available');
            notifySessionExpired('api.ts (no keycloak ref)');
          }
        }
        return Promise.reject(error);
      }
    );
  }

  // =============================================================================
  // HTTP Methods
  // =============================================================================

  async get(url: string, config?: AxiosRequestConfig) {
    return this.client.get(url, config);
  }

  async post(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return this.client.post(url, data, config);
  }

  async put(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return this.client.put(url, data, config);
  }

  async patch(url: string, data?: unknown, config?: AxiosRequestConfig) {
    return this.client.patch(url, data, config);
  }

  async delete(url: string, config?: AxiosRequestConfig) {
    return this.client.delete(url, config);
  }

  // --- httpOnly cookie session management ---

  async setSession(token: string): Promise<void> {
    await this.client.post('/api/auth/session', { token });
  }

  async clearSession(): Promise<void> {
    await this.client.delete('/api/auth/session');
  }

  // I18N is now handled entirely in the frontend via I18nContext
  // Translations are loaded from /locales/*.json files
  // No backend service needed

  async updateUserProfile(firstName: string, lastName?: string): Promise<TenantUser | unknown> {
    const response = await this.client.put('/api/tenant/users/me', {
      firstName: firstName.trim(),
      lastName: lastName?.trim() || ''
    });
    return response.data as TenantUser;
  }

  async getTenantUsers(): Promise<TenantUsersResponse> {
    const response = await this.client.get('/api/tenant/users');
    return response.data as TenantUsersResponse;
  }

  async createTenantUser(userData: {
    email: string;
    firstName: string;
    lastName: string;
    roles: string[];
    password: string;
    temporary?: boolean;
  }): Promise<TenantUser | unknown> {
    const response = await this.client.post('/api/tenant/users', userData);
    return response.data as TenantUser;
  }

  async createActivationCode(data: {
    email: string;
    plan?: string;
    duration_days?: number;
    notes?: string;
    tenant_name?: string;
  }): Promise<unknown> {
    const response = await this.client.post('/api/admin/activations', data);
    return response.data;
  }

  // Entities - Robots
  async getRobots(): Promise<Robot[]> {
    try {
      return await fetchAllPages<Robot>(
        async (offset, limit) => {
          const response = await this.client.get('/ngsi-ld/v1/entities', {
            params: { type: 'AutonomousMobileRobot', limit, offset },
            headers: { 'Accept': 'application/ld+json' },
          });
          return Array.isArray(response.data) ? response.data : [];
        },
        { label: 'AutonomousMobileRobot' },
      );
    } catch (error) {
      logger.warn('Error fetching robots:', error);
      return [];
    }
  }

  // =============================================================================
  // Sensor Management Methods - Using new API endpoints
  // =============================================================================

  async getSensors(): Promise<Sensor[]> {
    try {
      // Fetch sensors from PostgreSQL API only (NGSI-LD sensors are loaded
      // separately via getSDMEntityInstances('AgriSensor') in the viewer)
      const response = await this.client.get('/api/sensors');
      if (response.data?.sensors) {
        return response.data.sensors.map((s: any) => ({
          id: s.id,
          type: 'AgriSensor',
          name: {
            type: 'Property',
            value: s.name || s.external_id
          },
          location: s.location?.value || s.location,
          external_id: s.external_id,
          profile: s.profile
        }));
      }
      return [];
    } catch (error) {
      logger.warn('Error fetching sensors:', error);
      return [];
    }
  }

  // =============================================================================
  // Telemetry Methods
  // =============================================================================

  async getDeviceTelemetry(deviceId: string, params?: {
    start_time?: string;
    end_time?: string;
    limit?: number;
  }): Promise<any> {
    const response = await this.client.get(`/api/devices/${deviceId}/telemetry`, { params });
    return response.data;
  }

  async getDeviceLatestTelemetry(deviceId: string): Promise<any> {
    const response = await this.client.get(`/api/devices/${deviceId}/telemetry/latest`);
    return response.data;
  }

  async getDeviceTelemetryStats(deviceId: string, params?: {
    start_time?: string;
    end_time?: string;
  }): Promise<any> {
    const response = await this.client.get(`/api/devices/${deviceId}/telemetry/stats`, { params });
    return response.data;
  }

  // =============================================================================
  // Timeseries Reader Service Methods
  // =============================================================================

  /**
   * Get historical timeseries data for an entity from TimescaleDB.
   * Detects telemetry entities (IoT sensors) and routes to v2 endpoint for telemetry_events.
   */
  async getTimeseriesData(
    entityId: string,
    options: {
      start_time: string;
      end_time?: string;
      aggregation?: 'none' | 'hourly' | 'daily' | 'weekly' | 'monthly';
      attribute?: string;
      limit?: number;
    }
  ): Promise<{
    entity_id?: string;
    entity_urn?: string;
    series_kind?: string;
    start_time: string;
    end_time: string;
    aggregation: string;
    count: number;
    data?: Array<{
      timestamp: string;
      [attribute: string]: string | number;
    }>;
    timestamps?: string[];
    attributes?: Record<string, (number | null)[]>;
  }> {
    const isTelemetryEntity = entityId.startsWith('urn:ngsi-ld:AgriSensor:') ||
                               entityId.startsWith('urn:ngsi-ld:Sensor:') ||
                               entityId.startsWith('urn:ngsi-ld:Device:');
    const endpoint = isTelemetryEntity
      ? `/api/timeseries/v2/entities/${encodeURIComponent(entityId)}/data`
      : `/api/timeseries/entities/${entityId}/data`;

    const response = await this.client.get(endpoint, { params: options });
    return response.data;
  }

  /**
   * Get statistics for entity timeseries data
   */
  async getTimeseriesStats(
    entityId: string,
    options: {
      start_time: string;
      end_time?: string;
      attribute?: string;
    }
  ): Promise<{
    entity_id: string;
    start_time: string;
    end_time: string;
    stats: Record<string, {
      min: number | null;
      max: number | null;
      avg: number | null;
      count: number;
      first_observed: string | null;
      last_observed: string | null;
    }>;
  }> {
    const response = await this.client.get(`/api/timeseries/entities/${entityId}/stats`, {
      params: options,
    });
    return response.data;
  }

  // =============================================================================
  // NGSI-LD Subscriptions Methods
  // =============================================================================

  // =============================================================================
  // Device Commands Methods
  // =============================================================================

  // =============================================================================
  // SDM Integration Methods
  // =============================================================================
  // Note: getSDMEntities, getSDMEntitySchema, and createSDMEntity are defined later
  // to avoid duplication. See lines 1066-1094 for implementations.

  async getSDMEntityInstances(entityType: string, useCache: boolean = true): Promise<any[]> {
    const cacheKey = `entities:${entityType}`;

    // Check cache first
    if (useCache) {
      const cached = this.cache.get<unknown[]>(cacheKey);
      if (cached) {
        return cached as any[];
      }
    }

    try {
      // Orion-LD returns an array directly; retry applies per page
      const data = await fetchAllPages<any>(
        async (offset, limit) => {
          const response = await retryRequest(
            () => this.client.get('/ngsi-ld/v1/entities', {
              params: { type: entityType, limit, offset },
            }),
            3,
            1000
          );
          return Array.isArray(response.data) ? response.data : (response.data.instances || []);
        },
        { label: entityType },
      );

      // Cache for 60 seconds
      if (useCache) {
        this.cache.set(cacheKey, data, 60000);
      }

      return data;
    } catch (error: any) {
      // Clear cache on error
      this.cache.delete(cacheKey);
      throw error;
    }
  }

  async getSDMEntityInstancesPaginated(entityType: string, params?: { limit?: number; offset?: number }): Promise<{ instances: any[]; total: number; count: number }> {
    const queryParams = { ...params, type: entityType, count: 'true' };
    const response = await this.client.get('/ngsi-ld/v1/entities', { params: queryParams });
    const entities = Array.isArray(response.data) ? response.data : [];
    // Orion-LD returns total count in NGSILD-Results-Count header when count=true
    const totalHeader = response.headers?.['ngsild-results-count'];
    const total = totalHeader ? parseInt(totalHeader, 10) : entities.length;
    return { instances: entities, total, count: entities.length };
  }

  /** Same merge-patch as mergeSDMEntity: PATCH /attrs would drop attributes the entity lacks. */
  async updateSDMEntity(_entityType: string, entityId: string, updates: any): Promise<void> {
    await this.mergeSDMEntity(entityId, updates);
  }

  /** Merge-patch: creates or overwrites the given attributes (PATCH /attrs only updates existing ones). */
  async mergeSDMEntity(entityId: string, fragment: Record<string, unknown>): Promise<void> {
    await this.client.patch(`/ngsi-ld/v1/entities/${entityId}`, fragment, {
      headers: { 'Content-Type': 'application/json' },
    });
  }

  async deleteSDMEntityAttribute(entityId: string, attribute: string): Promise<void> {
    await this.client.delete(`/ngsi-ld/v1/entities/${entityId}/attrs/${encodeURIComponent(attribute)}`);
  }

  async getSDMEntityInstance(_entityType: string, entityId: string): Promise<any> {
    const response = await this.client.get(`/ngsi-ld/v1/entities/${entityId}`);
    return response.data;
  }

  async deleteSDMEntity(entityType: string, entityId: string): Promise<void> {
    // Only entity-manager may write parcels; its route also removes the parcel's zones.
    if (entityType === 'AgriParcel') {
      await this.client.delete(`/api/entities/parcels/${encodeURIComponent(entityId)}`);
      return;
    }
    await this.client.delete(`/ngsi-ld/v1/entities/${entityId}`);
  }

  // =============================================================================
  // Vercel Blob Upload Authorization
  // =============================================================================

  // =============================================================================
  // Asset Service (MinIO) - Replaces Vercel Blob
  // =============================================================================

  /**
   * Upload an asset (3D model or icon) to MinIO
   */
  async uploadAsset(
    formData: FormData,
    config?: { onUploadProgress?: (progressEvent: any) => void }
  ): Promise<{
    url: string;
    asset_id: string;
    key: string;
    size: number;
    content_type: string;
    tenant_id: string;
  }> {
    const response = await this.client.post('/api/assets/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      onUploadProgress: config?.onUploadProgress,
    });
    return response.data;
  }

  /**
   * Get a presigned URL for an asset
   */
  async getAssetUrl(
    assetId: string,
    assetType: 'model' | 'icon',
    extension?: string
  ): Promise<{ url: string; expires_in: number; asset_id: string }> {
    const params = new URLSearchParams({ type: assetType });
    if (extension) params.append('extension', extension);
    const response = await this.client.get(`/api/assets/${assetId}?${params}`);
    return response.data;
  }

  /**
   * Delete an asset from MinIO
   */
  async deleteAsset(
    assetId: string,
    assetType: 'model' | 'icon',
    extension?: string
  ): Promise<{ deleted: boolean; asset_id: string }> {
    const params = new URLSearchParams({ type: assetType });
    if (extension) params.append('extension', extension);
    const response = await this.client.delete(`/api/assets/${assetId}?${params}`);
    return response.data;
  }

  // =============================================================================
  // Heartbeat / Connection Status
  // =============================================================================

  // Note: getSensors() is defined above (line 150) using new API endpoints
  // This duplicate method has been removed to avoid TypeScript errors

  // Entities - Parcels
  async getMachines(): Promise<AgriculturalMachine[]> {
    try {
      // Query for AutonomousMobileRobot and ManufacturingMachine
      const listType = (type: string): Promise<any[]> =>
        fetchAllPages<any>(
          async (offset, limit) => {
            const response = await this.client.get('/ngsi-ld/v1/entities', {
              params: { type, options: 'keyValues', limit, offset },
              headers: { 'Accept': 'application/ld+json' }
            });
            return Array.isArray(response.data) ? response.data : [];
          },
          { label: type },
        ).catch((e) => {
          logger.warn(`[api] getMachines: failed to load ${type}`, e);
          return [];
        });
      const [robots, machines] = await Promise.all([
        listType('AutonomousMobileRobot'),
        listType('ManufacturingMachine'),
      ]);

      return [...robots, ...machines].map((m: any) => {
        // Determine type description based on entity type
        let typeDesc = 'Machinery';
        if (m.type === 'AutonomousMobileRobot') typeDesc = 'Autonomous Robot';
        else if (m.type === 'ManufacturingMachine') {
          const category = m.category?.value || m.category;
          typeDesc = category === 'implement' ? 'Implement' : 'Tractor';
        }

        return {
          id: m.id,
          name: m.name || 'Unnamed Machine',
          type: typeDesc,
          status: m.status || 'offline',
          location: m.location, // options=keyValues returns direct GeoJSON
          batteryLevel: m.batteryLevel || 100,
          task: m.currentTask || 'Idle',
          operationType: m.operationType,
          model3d: m.model3d
        };
      });
    } catch (error) {
      logger.warn('Error fetching machines:', error);
      return [];
    }
  }

  async getLivestock(): Promise<LivestockAnimal[]> {
    try {
      const payload = await fetchAllPages<any>(
        async (offset, limit) => {
          const response = await this.client.get('/ngsi-ld/v1/entities', {
            params: { type: 'LivestockAnimal', options: 'keyValues', limit, offset },
            headers: { 'Accept': 'application/ld+json' },
          });
          return Array.isArray(response.data) ? response.data : [];
        },
        { label: 'LivestockAnimal' },
      );

      return payload.map((l: any) => ({
        id: l.id,
        type: 'LivestockAnimal',
        name: l.name || 'Unnamed Animal',
        species: l.species || 'Unknown',
        status: l.status || 'healthy',
        location: l.location, // options=keyValues returns direct GeoJSON
        lastUpdate: l.modifiedAt || new Date().toISOString()
      }));
    } catch (error) {
      logger.warn('Error fetching livestock:', error);
      return [];
    }
  }

  async getWeatherStations(): Promise<WeatherStation[]> {
    try {
      const entities = await fetchAllPages<any>(
        async (offset, limit) => {
          const response = await this.client.get('/ngsi-ld/v1/entities', {
            params: { type: 'WeatherObserved', limit, offset },
            headers: { 'Accept': 'application/ld+json' },
          });
          return Array.isArray(response.data) ? response.data : [];
        },
        { label: 'WeatherObserved' },
      );
      // Map NGSI-LD response
      // Per-parcel closed-day series entities are not stations: discriminate by
      // the dailySummary attribute (#1043); the "-daily" id suffix stays as a
      // legacy guard.
      const isClosedDay = (w: any): boolean =>
        w?.dailySummary === true ||
        w?.dailySummary?.value === true ||
        String(w?.id ?? '').endsWith('-daily');
      const payload = entities.filter((w: any) => !isClosedDay(w));

      return payload.map((w: any) => ({
        id: w.id,
        type: 'WeatherStation',
        name: w.name || 'Weather Station',
        location: w.location, // options=keyValues returns direct GeoJSON
        readings: {
          temperature: w.temperature || 0,
          humidity: w.humidity || 0,
          pressure: w.pressure || 1013,
          windSpeed: w.windSpeed || 0
        },
        lastUpdate: w.modifiedAt || new Date().toISOString(),
        // Keeps the station under its parcel in the asset tree.
        ...pickParentRelationships(w),
      }));
    } catch (error) {
      logger.warn('Error fetching weather stations:', error);
      return [];
    }
  }

  async getParcels(): Promise<Parcel[]> {
    try {
      return await fetchAllPages<Parcel>(
        async (offset, limit) => {
          const response = await this.client.get('/ngsi-ld/v1/entities', {
            params: { type: 'AgriParcel', limit, offset },
            headers: {
              'Accept': 'application/json',
              'Link': `<${config.external.contextUrl}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"`,
            },
          });
          return Array.isArray(response.data) ? response.data : [];
        },
        { label: 'AgriParcel' },
      );
    } catch (error) {
      logger.warn('Error fetching parcels:', error);
      return [];
    }
  }

  async getTenantUsage(): Promise<TenantUsageSummary> {
    const response = await this.client.get('/api/admin/tenant-usage');
    return response.data;
  }

  async getGrafanaLink(params?: { dashboard?: string }): Promise<GrafanaLink> {
    const response = await this.client.get('/entity-manager/integrations/grafana/link', {
      params,
    });
    return response.data;
  }

  // Parcel writes live in `parcelApi` (routed through the entity-manager core API).
  // The former direct-Orion createParcel/updateParcel/deleteParcel here were dead
  // (no callers) and bypassed the single source of truth — removed.

  // =============================================================================
  // Agricultural Machines (ManufacturingMachine/AgriOperation)
  // =============================================================================

  // =============================================================================
  // Livestock Animals
  // =============================================================================

  // =============================================================================
  // Weather Stations
  // =============================================================================

  // =============================================================================
  // Cadastral API - Parcel Management
  // =============================================================================
  // DEPRECATED: Use parcelApi.ts instead for all parcel operations
  // Legacy methods removed - parcelApi.ts provides Orion-LD First architecture

  // =============================================================================
  // Terrain 3D Service - REMOVED
  // =============================================================================
  // El sistema antiguo de generación de terrain bajo demanda ha sido eliminado.
  // Ahora usamos providers externos (IGN/IDENA) directamente en CesiumMap.
  // =============================================================================

  // =============================================================================
  // Asset Digitization Service
  // =============================================================================

  // Terms and Conditions
  async getTerms(language: string): Promise<{ content: string; last_updated: string; language: string }> {
    const response = await this.client.get(`/api/terms/${language}`);
    return response.data;
  }

  async saveTerms(language: string, content: string): Promise<{ success: boolean; message: string }> {
    const response = await this.client.post(`/api/admin/terms/${language}`, {
      content,
      language,
    });
    return response.data;
  }

  // =============================================================================
  // Weather Data Methods
  // =============================================================================

  async getWeatherLocations(): Promise<any[]> {
    try {
      const response = await this.client.get('/api/weather/locations');
      return response.data?.locations || [];
    } catch (error) {
      logger.warn('Error fetching weather locations:', error);
      return [];
    }
  }

  async searchMunicipalities(query: string, limit: number = 20): Promise<{ municipalities: any[]; count: number }> {
    try {
      const url = '/api/weather/municipalities/search';
      const fullUrl = `${this.client.defaults.baseURL || ''}${url}`;
      logger.debug('[API] Searching municipalities:', { query, limit, url, fullUrl });
      const response = await this.client.get(url, {
        params: { q: query, limit },
      });
      logger.debug('[API] Municipalities search successful:', response.data);
      return {
        municipalities: response.data?.municipalities || [],
        count: response.data?.count || 0,
      };
    } catch (error: any) {
      logger.error('[API] Error searching municipalities:', error);
      logger.error('[API] Error details:', {
        status: error.response?.status,
        data: error.response?.data,
        message: error.message,
        url: error.config?.url,
        baseURL: error.config?.baseURL,
        headers: error.config?.headers,
      });
      // Don't silently return empty - let the error propagate so UI can show it
      throw error;
    }
  }

  async getNearestMunicipality(latitude: number, longitude: number, maxDistanceKm: number = 50): Promise<any> {
    try {
      const response = await this.client.get('/api/weather/municipality/near', {
        params: { latitude, longitude, max_distance_km: maxDistanceKm },
      });
      return response.data;
    } catch (error: any) {
      // 404 = no municipality resolvable for these coordinates — not an error
      // worth surfacing as a console error; callers treat null as "no data".
      if (error?.response?.status === 404) {
        return null;
      }
      logger.error('[API] Error getting nearest municipality:', error);
      throw error;
    }
  }

  async getLatestWeatherObservations(params?: {
    municipality_code?: string;
    source?: string;
    data_type?: string;
  }): Promise<any[]> {
    try {
      const response = await this.client.get('/api/weather/observations/latest', { params });
      return response.data?.observations || [];
    } catch (error) {
      logger.warn('Error fetching latest weather observations:', error);
      return [];
    }
  }

  async getWeatherObservations(params?: {
    municipality_code?: string;
    source?: string;
    data_type?: string;
    start_date?: string;
    end_date?: string;
    limit?: number;
  }): Promise<{ observations: any[]; count: number }> {
    try {
      const response = await this.client.get('/api/weather/observations', { params });
      return {
        observations: response.data?.observations || [],
        count: response.data?.count || 0,
      };
    } catch (error) {
      logger.warn('Error fetching weather observations:', error);
      return { observations: [], count: 0 };
    }
  }

  /**
   * Get downscaled weather for a specific parcel from the canonical API.
   * Applies altitude/aspect/slope corrections automatically.
   */
  async getParcelWeather(parcelId: string, params?: {
    source?: string;
    data_type?: string;
    limit?: number;
  }): Promise<{
    parcel_id: string;
    municipality_code: string;
    municipality_name: string;
    downscaling: string;
    interpolation: string;
    station_count: number;
    parcel_altitude_m: number;
    station_altitude_m: number;
    parcel_aspect_deg: number;
    parcel_slope_deg: number;
    contributing_stations: Array<{
      municipality_code: string;
      municipality_name?: string;
      distance_km: number;
      station_altitude_m: number;
    }>;
    observations: Array<{
      observed_at: string;
      temp_avg?: number;
      temp_min?: number;
      temp_max?: number;
      humidity_avg?: number;
      precip_mm?: number;
      precip_probability?: number;
      solar_rad_w_m2?: number;
      solar_rad_ghi_w_m2?: number;
      solar_rad_dni_w_m2?: number;
      eto_mm?: number;
      wind_speed_ms?: number;
      wind_gusts_ms?: number;
      wind_direction_deg?: number;
      pressure_hpa?: number;
      gdd_accumulated?: number;
      delta_t?: number;
      soil_moisture_0_10cm?: number;
      soil_moisture_10_40cm?: number;
      source?: string;
      data_type?: string;
      municipality_code?: string;
    }>;
  }> {
    try {
      const response = await this.client.get(
        `/api/weather/parcel/${encodeURIComponent(parcelId)}`,
        { params }
      );
      return response.data;
    } catch (error) {
      logger.warn('Error fetching parcel weather:', error);
      throw error;
    }
  }

  async getParcelAgroStatus(parcelId: string): Promise<AgroStatusResponse> {
    try {
      const response = await this.client.get(`/api/weather/parcel/${parcelId}/agro-status`);
      return response.data;
    } catch (error) {
      logger.error('Agro-status endpoint failed:', error);
      throw error;
    }
  }

  async getWeatherAlerts(params?: {
    municipality_code?: string;
    alert_type?: string;
    active_only?: boolean;
  }): Promise<{ alerts: any[]; count: number }> {
    try {
      const response = await this.client.get('/api/weather/alerts', { params });
      return {
        alerts: response.data?.alerts || [],
        count: response.data?.count || 0,
      };
    } catch (error) {
      logger.warn('Error fetching weather alerts:', error);
      return { alerts: [], count: 0 };
    }
  }

  // =============================================================================
  // Risk Management Methods
  // =============================================================================

  // =============================================================================
  // Alert Methods (módulo risk federado — entidad Alert en Orion-LD)
  // =============================================================================

  async getActiveAlerts(params?: { category?: string; severity?: string; limit?: number }): Promise<AlertEntity[]> {
    try {
      const response = await this.client.get('/api/risk/alerts', { params });
      return (response.data?.alerts ?? []).map(normalizeAlert);
    } catch (error) {
      logger.warn('Error fetching alerts:', error);
      return [];
    }
  }

  // =============================================================================
  // SDM Integration - Entity Types and Management
  // =============================================================================

  async createSDMEntity(_entityType: string, entity: any): Promise<any> {
    // No @context in body — gateway injects Link/context with the platform context.
    const response = await this.client.post('/ngsi-ld/v1/entities', entity);
    return response.data;
  }

  /**
   * Create an IoT entity via SDM Integration Service.
   * This provisions the entity in Orion-LD AND in the IoT Agent in one call,
   * returning MQTT credentials for the physical device.
   */
  async createSDMIoTEntity(entityType: string, body: Record<string, unknown>): Promise<any> {
    const response = await this.client.post(`/sdm/entities/${entityType}/instances`, body);
    return response.data;
  }

  // =============================================================================
  // Parent Entities (for hierarchy)
  // =============================================================================

  async getParentEntities(entityType?: string): Promise<any[]> {
    try {
      const params = entityType ? { type: entityType } : {};
      const response = await this.client.get('/api/entities/parents', { params });
      return Array.isArray(response.data?.entities) ? response.data.entities : [];
    } catch (error) {
      logger.warn('Error fetching parent entities:', error);
      return [];
    }
  }

  // =============================================================================
  // Robot Provisioning (UUID + ROS namespace; network via nkz-module-vpn Claim Code)
  // =============================================================================

  async provisionRobot(robotData: {
    name: string;
    location: any;
    robotType?: string;
    model?: string;
    manufacturer?: string;
    serialNumber?: string;
    [key: string]: any;
  }): Promise<{
    robot: any;
    credentials: {
      robot_uuid: string;
      ros_namespace: string;
    };
    info?: string;
  }> {
    const response = await this.client.post('/entity-manager/api/robots/provision', robotData);
    return response.data;
  }
}

const apiService = new ApiService();

// Export methods for cache management
export const clearApiCache = () => {
  (apiService as any).cache.clear();
};

export const invalidateEntityCache = (entityType: string, entityId?: string) => {
  const cache = (apiService as any).cache as SimpleCache;
  if (entityId) {
    cache.delete(`entity:${entityType}:${entityId}`);
  }
  cache.delete(`entities:${entityType}`);
};

export const api = apiService;
export default apiService;
