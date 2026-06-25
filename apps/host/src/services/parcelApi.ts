// =============================================================================
// Parcel API Service - Sprint 4
// =============================================================================
// Service for managing AgriParcel entities in Orion-LD
// Implements attribute inheritance for management zones
import { logger } from '@/utils/logger';

import axios, { AxiosInstance } from 'axios';
import type { Parcel } from '@/types';
import { getConfig } from '@/config/environment';
import { calculatePolygonAreaHectares } from '@/utils/geo';
import { api } from '@/services/api';

/* eslint-disable @typescript-eslint/no-explicit-any */
const config = getConfig();

// Function to get current token
const getAuthToken = (): string | null => {
    if (typeof window !== 'undefined') {
        const keycloakInstance = (window as any).keycloak;
        if (keycloakInstance && keycloakInstance.token) {
            return keycloakInstance.token;
        }
    }
    return null;
};

class ParcelApiService {
    private client: AxiosInstance;

    constructor() {
        this.client = axios.create({
            baseURL: config.api.baseUrl,
            timeout: config.api.timeout,
            withCredentials: true,
        });

        // Request interceptor to add auth token
        this.client.interceptors.request.use(
            (requestConfig) => {
                const token = getAuthToken();
                let tenantId = 'nekazari'; // Default fallback

                if (token) {
                    requestConfig.headers.Authorization = `Bearer ${token}`;

                    // Try to extract tenant from token
                    try {
                        const base64Url = token.split('.')[1];
                        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
                        const jsonPayload = decodeURIComponent(window.atob(base64).split('').map(function (c) {
                            return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
                        }).join(''));
                        const decoded = JSON.parse(jsonPayload);

                        // Check common tenant fields (support both snake_case and kebab-case)
                        tenantId = decoded['tenant-id'] || decoded.tenant_id || decoded.tenantId || decoded.tenant || 'nekazari';
                        logger.debug('[ParcelAPI] Using Tenant ID:', tenantId);
                    } catch (e) {
                        logger.warn('[ParcelAPI] Failed to decode token for tenant extraction', e);
                    }
                }

                // Add Tenant ID header
                requestConfig.headers['X-Tenant-ID'] = tenantId;
                return requestConfig;
            },
            (error) => Promise.reject(error)
        );

        // Response interceptor for error handling and token refresh
        this.client.interceptors.response.use(
            (response) => response,
            async (error) => {
                const originalRequest = error.config;

                // Handle 401 Unauthorized - try to refresh token
                if (error.response?.status === 401 && !originalRequest._retry) {
                    originalRequest._retry = true;

                    try {
                        // Try to refresh Keycloak token if available
                        if (typeof window !== 'undefined') {
                            const keycloakInstance = (window as any).keycloak;
                            if (keycloakInstance && typeof keycloakInstance.updateToken === 'function') {
                                const refreshed = await keycloakInstance.updateToken(30); // Refresh if expires in 30s
                                if (refreshed) {
                                    logger.debug('[ParcelAPI] Token refreshed, retrying request');
                                    // Update token in request and httpOnly cookie
                                    const newToken = keycloakInstance.token;
                                    originalRequest.headers.Authorization = `Bearer ${newToken}`;
                                    api.setSession(newToken).catch(() => {});
                                    return this.client(originalRequest);
                                }
                            }
                        }

                        // If refresh failed, redirect to login
                        logger.warn('[ParcelAPI] Token refresh failed, redirecting to login');
                        if (typeof window !== 'undefined') {
                            window.location.href = '/login';
                        }
                    } catch (refreshError) {
                        logger.error('[ParcelAPI] Error refreshing token:', refreshError);
                        if (typeof window !== 'undefined') {
                            window.location.href = '/login';
                        }
                        return Promise.reject(refreshError);
                    }
                }

                return Promise.reject(error);
            }
        );
    }

    /**
   * Convert NGSI-LD entity to frontend Parcel (simplified format)
   */
    private fromNGSILD(entity: any): Parcel {
        const geometry = entity.location?.value;
        
        // Calculate area from geometry if not provided
        let area = entity.area?.value;
        if (!area && geometry && geometry.type === 'Polygon' && geometry.coordinates) {
            try {
                area = calculatePolygonAreaHectares(geometry);
            } catch (error) {
                logger.warn('Error calculating area from geometry:', error);
            }
        }

        return {
            id: entity.id,
            type: entity.type,
            name: entity.name?.value || null,
            category: entity.category?.value || 'cadastral',
            geometry: geometry,
            municipality: entity.municipality?.value || '',
            province: entity.province?.value || '',
            cropType: entity.cropType?.value || '',
            cadastralReference: entity.cadastralReference?.value,
            // Canonical relationship is `hasAgriParcel` (child→parent); keep `refParent`
            // as a fallback for legacy entities written before the core refactor.
            refParent: entity.hasAgriParcel?.object ?? entity.refParent?.object,
            children: entity.children?.object,
            ndviEnabled: entity.ndviEnabled?.value !== false,
            notes: entity.notes?.value,
            refFarm: entity.refFarm?.object,
            area: area,
            generationMethod: entity.generationMethod?.value as 'grid' | 'manual' | 'ai' | undefined,
            aiModel: entity.aiModel?.value,
            confidence: entity.confidence?.value,
        };
    }

    /**
     * Get all parcels for current tenant
     */
    async getParcels(): Promise<Parcel[]> {
        try {
            const response = await this.client.get('/ngsi-ld/v1/entities', {
                params: { type: 'AgriParcel' },
                headers: {
                    'Accept': 'application/json',
                    'Link': `<${config.external.contextUrl}>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"`,
                },
            });

            const entities = Array.isArray(response.data) ? response.data : [];
            return entities.map(e => this.fromNGSILD(e));
        } catch (error) {
            logger.error('Error fetching parcels:', error);
            return [];
        }
    }

    /**
     * Create a new cadastral parcel (parent) through the entity-manager core API.
     *
     * The server generates the canonical URN id (UUID) and dedups by
     * cadastralReference, so a create may return an existing parcel's id.
     */
    async createParcel(parcel: Partial<Parcel>): Promise<Parcel> {
        const payload: Record<string, any> = {
            geometry: parcel.geometry,
            category: parcel.category || 'cadastral',
        };
        if (parcel.name) payload.name = parcel.name;
        if (parcel.municipality) payload.municipality = parcel.municipality;
        if (parcel.province) payload.province = parcel.province;
        if (parcel.cropType) payload.cropType = parcel.cropType;
        if (parcel.cadastralReference) payload.cadastralReference = parcel.cadastralReference;
        if (parcel.area != null) payload.area = parcel.area;
        if (parcel.notes) payload.notes = parcel.notes;
        if (parcel.generationMethod) payload.generationMethod = parcel.generationMethod;

        const resp = await this.client.post('/api/entities/parcels', payload);
        return { ...(parcel as Parcel), id: resp.data.id, category: parcel.category || 'cadastral' };
    }

    /**
     * Create management zones (children) with attribute inheritance
     * 
     * IMPORTANT: Children inherit from parent:
     * - cropType
     * - refFarm (if exists)
     * - municipality
     * - province
     * - ndviEnabled
     */
    async createZones(parentParcel: Parcel, zones: Partial<Parcel>[]): Promise<Parcel[]> {
        const created: Parcel[] = [];
        for (const zone of zones) {
            const payload: Record<string, any> = {
                geometry: zone.geometry,
                category: 'managementZone',
                name: zone.name || `Zona ${Date.now()}`,
                municipality: zone.municipality || parentParcel.municipality,
                province: zone.province || parentParcel.province,
                cropType: zone.cropType !== undefined ? zone.cropType : (parentParcel.cropType || ''),
                generationMethod: zone.generationMethod || 'grid',
                refParent: parentParcel.id,
            };
            const resp = await this.client.post('/api/entities/parcels', payload);
            created.push({ ...(zone as Parcel), id: resp.data.id, category: 'managementZone', refParent: parentParcel.id });
        }
        return created;
    }

    /**
     * Update an existing parcel via entity-manager.
     */
    async updateParcel(id: string, updates: Partial<Parcel>): Promise<void> {
        const payload: Record<string, any> = {};
        const props: (keyof Parcel)[] = ['name', 'municipality', 'province', 'cropType', 'cadastralReference', 'notes', 'generationMethod'];
        for (const k of props) if (updates[k] !== undefined) payload[k] = updates[k];
        if (updates.geometry) payload.geometry = updates.geometry;
        if (updates.ndviEnabled !== undefined) payload.ndviEnabled = updates.ndviEnabled;
        await this.client.patch(`/api/entities/parcels/${encodeURIComponent(id)}`, payload);
    }

    /**
     * Delete a parcel via entity-manager (cascades to child zones).
     */
    async deleteParcel(id: string): Promise<void> {
        await this.client.delete(`/api/entities/parcels/${encodeURIComponent(id)}`);
    }

    /**
     * Delete a single management zone. The parent no longer tracks a children
     * list — the child→parent relationship lives on the zone — so deleting the
     * zone is all that is required.
     */
    async deleteZone(zoneId: string, _parentId: string): Promise<void> {
        await this.deleteParcel(zoneId);
    }

    /**
     * Create zones from AI-generated subdivisions
     */
    async createZonesFromAI(
        parentParcel: Parcel,
        zones: Array<{
            geometry: any;
            name?: string;
            aiModel: string;
            confidence: number;
        }>
    ): Promise<Parcel[]> {
        const zoneParcels: Partial<Parcel>[] = zones.map(zone => ({
            category: 'managementZone',
            refParent: parentParcel.id,
            municipality: parentParcel.municipality,
            province: parentParcel.province,
            cropType: parentParcel.cropType,
            geometry: zone.geometry,
            name: zone.name || `Zona AI ${Date.now()}`,
            generationMethod: 'ai',
            aiModel: zone.aiModel,
            confidence: zone.confidence,
        }));

        return this.createZones(parentParcel, zoneParcels);
    }

    /**
     * Get a single parcel by ID
     */
    async getParcel(id: string): Promise<Parcel | null> {
        try {
            const response = await this.client.get(`/ngsi-ld/v1/entities/${id}`, {
                headers: { 'Accept': 'application/ld+json' },
            });

            return this.fromNGSILD(response.data);
        } catch (error) {
            logger.error('Error fetching parcel:', error);
            return null;
        }
    }
}

export const parcelApi = new ParcelApiService();
