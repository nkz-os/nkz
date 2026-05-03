// =============================================================================
// Weather Page - Complete Weather and Agronomic Dashboard
// =============================================================================
// Municipality state is owned at the page level so Widget and AgroPanel
// stay synchronized. A parcel selector enables per-parcel spatially-corrected
// weather via GET /api/weather/parcel/{id}.

import React, { useState, useEffect, useCallback } from 'react';
import { WeatherWidget } from '@/components/WeatherWidget';
import { WeatherAgroPanel } from '@/components/WeatherAgroPanel';
import { WeatherStationsList } from '@/components/WeatherStationsList';
import { useI18n } from '@/context/I18nContext';
import { useTenantMunicipality } from '@/hooks/useTenantMunicipality';
import api from '@/services/api';
import { logger } from '@/utils/logger';
import { MapPin, ChevronDown, Sprout } from 'lucide-react';

interface ParcelOption {
  id: string;
  name: string;
  municipality_code?: string;
  municipality_name?: string;
}

export const Weather: React.FC = () => {
  const { t } = useI18n();
  const { municipality: tenantMunicipality } = useTenantMunicipality();

  // Shared municipality state
  const [municipalityCode, setMunicipalityCode] = useState<string | undefined>(
    tenantMunicipality?.code
  );
  const [municipalityName, setMunicipalityName] = useState<string | undefined>(
    tenantMunicipality?.name
  );

  // Parcel state
  const [parcels, setParcels] = useState<ParcelOption[]>([]);
  const [selectedParcelId, setSelectedParcelId] = useState<string | undefined>();
  const [selectedParcelName, setSelectedParcelName] = useState<string | undefined>();
  const [showParcelDropdown, setShowParcelDropdown] = useState(false);

  // Sync from tenant municipality
  useEffect(() => {
    if (tenantMunicipality?.code && !municipalityCode) {
      setMunicipalityCode(tenantMunicipality.code);
      setMunicipalityName(tenantMunicipality.name);
    }
  }, [tenantMunicipality]);

  // Load parcels for the tenant
  useEffect(() => {
    let cancelled = false;
    const loadParcels = async () => {
      try {
        const result = await api.getParcels();
        const items: ParcelOption[] = (result || [])
          .filter((e: any) => e?.id)
          .map((e: any) => ({
            id: e.id,
            name: e.name?.value || e.name || e.id?.split(':')?.pop() || 'Parcel',
            municipality_code: e.municipalityCode?.value || e.municipalityCode,
            municipality_name: undefined,
          }));
        if (!cancelled) {
          setParcels(items);
          if (items.length === 0) {
            setSelectedParcelId(undefined);
            setSelectedParcelName(undefined);
          }
        }
      } catch (err) {
        logger.warn('Error loading parcels for weather page:', err);
      }
    };
    loadParcels();
    return () => { cancelled = true; };
  }, []);

  const handleMunicipalitySelect = useCallback((code: string, name: string) => {
    setMunicipalityCode(code);
    setMunicipalityName(name);
  }, []);

  const handleParcelSelect = useCallback((parcel: ParcelOption) => {
    setSelectedParcelId(parcel.id);
    setSelectedParcelName(parcel.name);
    setShowParcelDropdown(false);
    if (parcel.municipality_code) {
      setMunicipalityCode(parcel.municipality_code);
      setMunicipalityName(parcel.municipality_name || parcel.municipality_code);
    }
  }, []);

  return (
    <>
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">
          {t('weather.page_title')}
        </h1>
        <p className="text-gray-600">
          {t('weather.page_subtitle')}
        </p>
      </div>

      {/* Parcel selector */}
      {parcels.length > 0 && (
        <div className="mb-4 relative">
          <button
            onClick={() => setShowParcelDropdown(!showParcelDropdown)}
            className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-300 rounded-lg shadow-sm hover:border-green-400 transition text-sm"
          >
            <Sprout className="w-4 h-4 text-green-600" />
            <span className="text-gray-700">
              {selectedParcelName || t('weather.select_parcel') || 'Seleccionar parcela'}
            </span>
            <ChevronDown className="w-4 h-4 text-gray-400" />
          </button>
          {selectedParcelId && (
            <button
              onClick={() => {
                setSelectedParcelId(undefined);
                setSelectedParcelName(undefined);
              }}
              className="ml-2 text-xs text-gray-500 hover:text-gray-700 underline"
            >
              {t('weather.clear_parcel') || 'Limpiar'}
            </button>
          )}
          {showParcelDropdown && (
            <div className="absolute z-10 mt-1 w-80 max-h-64 overflow-y-auto bg-white border border-gray-200 rounded-lg shadow-lg">
              {parcels.map((p) => (
                <button
                  key={p.id}
                  onClick={() => handleParcelSelect(p)}
                  className={`w-full px-4 py-2 text-left text-sm hover:bg-green-50 transition flex items-center gap-2 ${
                    selectedParcelId === p.id ? 'bg-green-100 font-medium' : ''
                  }`}
                >
                  <MapPin className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />
                  <span className="truncate">{p.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="space-y-6">
        <WeatherWidget
          municipalityCode={municipalityCode}
          municipalityName={municipalityName}
          parcelId={selectedParcelId}
          onMunicipalitySelect={handleMunicipalitySelect}
        />

        <WeatherAgroPanel
          municipalityCode={municipalityCode}
          municipalityName={municipalityName}
          parcelId={selectedParcelId}
          onMunicipalitySelect={handleMunicipalitySelect}
        />

        <WeatherStationsList />
      </div>
    </>
  );
};
