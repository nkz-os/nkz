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
import { useTenantHomeLocation } from '@/hooks/useTenantHomeLocation';

export const Weather: React.FC = () => {
  const { t } = useI18n();
  const { location: tenantHome } = useTenantHomeLocation();

  // Shared municipality state
  const [municipalityCode, setMunicipalityCode] = useState<string | undefined>(
    tenantHome?.municipalityCode
  );
  const [municipalityName, setMunicipalityName] = useState<string | undefined>(
    tenantHome?.name
  );

  // Parcel state (synced from widget, used by WeatherAgroPanel)
  const [selectedParcelId, setSelectedParcelId] = useState<string | undefined>();
  const [, setSelectedParcelName] = useState<string | undefined>();

  // Sync from tenant home location
  useEffect(() => {
    if (tenantHome?.municipalityCode && !municipalityCode) {
      setMunicipalityCode(tenantHome.municipalityCode);
      setMunicipalityName(tenantHome.name || '');
    }
  }, [tenantHome]);

  const handleMunicipalitySelect = useCallback((code: string, name: string) => {
    setMunicipalityCode(code);
    setMunicipalityName(name);
  }, []);

  const handleParcelSelect = useCallback((parcelId: string, parcelName: string) => {
    setSelectedParcelId(parcelId);
    setSelectedParcelName(parcelName);
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

      <div className="space-y-6">
        <WeatherWidget
          municipalityCode={municipalityCode}
          municipalityName={municipalityName}
          parcelId={selectedParcelId}
          onMunicipalitySelect={handleMunicipalitySelect}
          onParcelSelect={handleParcelSelect}
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
