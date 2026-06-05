// =============================================================================
// Weather Agro Panel Component - Triple Panel Agronomic Dashboard
// =============================================================================
// Widget para mostrar información agronómica crítica: Pulverización, Tempero, Riego
// =============================================================================

import React, { useState, useEffect } from 'react';
import { 
/* eslint-disable @typescript-eslint/no-explicit-any */
  Cloud, 
  Database,
  Droplets, 
  Wind, 
  Thermometer, 
  Sprout, 
  AlertCircle, 
  CheckCircle2, 
  XCircle,
  Loader2,
  RefreshCw,
  MapPin,
  Search
} from 'lucide-react';
import api from '@/services/api';
import { parcelApi } from '@/services/parcelApi';
import { useI18n } from '@/context/I18nContext';
import { useTenantHomeLocation } from '@/hooks/useTenantHomeLocation';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';

interface WeatherObservation {
  observed_at: string;
  temp_avg?: number;
  humidity_avg?: number;
  wind_speed_ms?: number;
  wind_direction_deg?: number;
  precip_mm?: number;
  eto_mm?: number;
  delta_t?: number;
  pressure_hpa?: number;
  soil_moisture_0_10cm?: number;
  metadata?: {
    weather_code?: string;
    precipitation_probability?: number;
  };
}

interface ParcelSensor {
  id: string;
  moisture?: {
    type: 'Property';
    value: number;
  };
  location?: {
    type: 'GeoProperty';
    value: {
      type: 'Point';
      coordinates: [number, number];
    };
  };
}

interface AgroStatusSemaphore {
  semaphores: {
    spraying: SprayingCondition;
    workability: WorkabilityCondition;
    irrigation: IrrigationCondition;
  };
  source_confidence: string;
  metrics?: {
    temperature?: number;
    humidity?: number;
    delta_t?: number;
    water_balance?: number;
    wind_speed?: number;
    wind_gusts?: number;
    precip_probability?: number;
    spraying_reason?: string;
  };
  downscaling?: string;
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
  inversion_risk?: boolean;
  timestamp?: string;
}

interface WeatherAgroPanelProps {
  municipalityCode?: string;
  municipalityName?: string;
  parcelId?: string; // Optional: if provided, use parcel-specific data
  onMunicipalitySelect?: (code: string, name: string) => void;
}

type SprayingCondition = 'optimal' | 'caution' | 'not_suitable' | 'unknown';
type WorkabilityCondition = 'optimal' | 'too_wet' | 'too_dry' | 'caution' | 'unknown';
type IrrigationCondition = 'satisfied' | 'alert' | 'deficit' | 'unknown';

export const WeatherAgroPanel: React.FC<WeatherAgroPanelProps> = ({
  municipalityCode,
  municipalityName,
  parcelId,
  onMunicipalitySelect,
}) => {
  const { t } = useI18n();

  const { location: homeLocation, loading: _loadingHomeLocation } = useTenantHomeLocation();
  
  // Load saved municipality from localStorage on mount
  const getSavedMunicipality = (): { code?: string; name?: string } => {
    try {
      const saved = localStorage.getItem('weatherAgroPanel_municipality');
      if (saved) {
        return JSON.parse(saved);
      }
    } catch (e) {
      logger.warn('Error loading saved municipality:', e);
    }
    return {};
  };
  
  const savedMunicipality = getSavedMunicipality();
  
  // Priority: prop > saved > tenant municipality
  const [selectedMunicipalityCode, setSelectedMunicipalityCode] = useState<string | undefined>(
    municipalityCode || savedMunicipality.code || homeLocation?.municipalityCode
  );
  const [selectedMunicipalityName, setSelectedMunicipalityName] = useState<string | undefined>(
    municipalityName || savedMunicipality.name || homeLocation?.name
  );

  // Parcel state — preferred over municipality for per-parcel virtual weather stations
  const [parcels, setParcels] = useState<Array<{ id: string; name: string }>>([]);
  const [selectedParcelId, setSelectedParcelId] = useState<string | undefined>(parcelId);
  const [selectedParcelName, setSelectedParcelName] = useState<string>('');
  const [showParcelSearch, setShowParcelSearch] = useState(false);

  // Load tenant parcels on mount — auto-select first as fallback
  useEffect(() => {
    const loadParcels = async () => {
      try {
        const result = await parcelApi.getParcels();
        if (result && result.length > 0) {
          const list = result.map((p: any) => ({
            id: p.id || p.parcelId || '',
            name: p.name?.value || p.name || p.parcelName || 'Sin nombre',
          }));
          setParcels(list);
          if (!parcelId && !selectedParcelId && list.length > 0) {
            setSelectedParcelId(list[0].id);
            setSelectedParcelName(list[0].name);
          }
        }
      } catch (err) {
        logger.warn('[WeatherAgroPanel] Error loading parcels:', err);
      }
    };
    loadParcels();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const [currentWeather, setCurrentWeather] = useState<WeatherObservation | null>(null);
  const [historicalWeather, setHistoricalWeather] = useState<WeatherObservation[]>([]);
  const [parcelSensors, setParcelSensors] = useState<ParcelSensor[]>([]);
  const [agroStatus, setAgroStatus] = useState<AgroStatusSemaphore | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showMunicipalitySearch, setShowMunicipalitySearch] = useState(false);
  const [searchingMunicipalities, setSearchingMunicipalities] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [municipalities, setMunicipalities] = useState<Array<{ code: string; name: string; province?: string; fullName?: string }>>([]);
  
  // Save municipality to localStorage when it changes
  useEffect(() => {
    if (selectedMunicipalityCode && selectedMunicipalityName) {
      try {
        localStorage.setItem('weatherAgroPanel_municipality', JSON.stringify({
          code: selectedMunicipalityCode,
          name: selectedMunicipalityName,
        }));
      } catch (e) {
        logger.warn('Error saving municipality to localStorage:', e);
      }
    }
  }, [selectedMunicipalityCode, selectedMunicipalityName]);
  
  // Update internal state when props or tenant municipality change
  useEffect(() => {
    if (municipalityCode) {
      setSelectedMunicipalityCode(municipalityCode);
    } else if (homeLocation && !selectedMunicipalityCode) {
      // Auto-set from tenant home location if no prop provided
      setSelectedMunicipalityCode(homeLocation.municipalityCode || '');
    }
    
    if (municipalityName) {
      setSelectedMunicipalityName(municipalityName);
    } else if (homeLocation && !selectedMunicipalityName) {
      // Auto-set from tenant home location if no prop provided
      setSelectedMunicipalityName(homeLocation.name || null);
    }
  }, [municipalityCode, municipalityName, homeLocation]);

  // Load weather data
  useEffect(() => {
    const effectiveParcelId = selectedParcelId || parcelId;
    if (selectedMunicipalityCode || effectiveParcelId) {
      loadWeatherData();
    }
  }, [selectedMunicipalityCode, selectedParcelId, parcelId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load parcel sensors and agro-status when parcel is selected
  useEffect(() => {
    const effectiveParcelId = selectedParcelId || parcelId;
    if (effectiveParcelId) {
      loadParcelSensors();
      loadAgroStatus();
    } else {
      setAgroStatus(null);
    }
  }, [selectedParcelId, parcelId]);

  const loadAgroStatus = async () => {
    const effectiveParcelId = selectedParcelId || parcelId;
    if (!effectiveParcelId) return;
    try {
      const resp = await api.getParcelAgroStatus(effectiveParcelId);
      setAgroStatus(resp);
    } catch (err) {
      logger.warn('Agro-status API failed, falling back to local calculation:', err);
      setAgroStatus(null);
    }
  };

  // Map backend semaphore value to UI color
  const semaphoreColor = (value: string): string => {
    switch (value) {
      case 'optimal': case 'satisfied': return 'green';
      case 'caution': case 'alert': return 'yellow';
      case 'not_suitable': case 'too_wet': case 'too_dry': case 'deficit': return 'red';
      default: return 'gray';
    }
  };

  const semaphoreLabel = (type: string, value: string): string => {
    const labels: Record<string, Record<string, string>> = {
      spraying: {
        optimal: t('weather.agro_panel.conditions.spraying_optimal'),
        caution: t('weather.agro_panel.conditions.spraying_caution'),
        not_suitable: t('weather.agro_panel.conditions.spraying_not_suitable'),
        unknown: t('weather.agro_panel.conditions.unknown'),
      },
      workability: {
        optimal: t('weather.agro_panel.conditions.workability_optimal'),
        too_wet: t('weather.agro_panel.conditions.workability_too_wet'),
        too_dry: t('weather.agro_panel.conditions.workability_too_dry'),
        caution: t('weather.agro_panel.conditions.workability_caution'),
        unknown: t('weather.agro_panel.conditions.unknown'),
      },
      irrigation: {
        satisfied: t('weather.agro_panel.conditions.irrigation_satisfied'),
        alert: t('weather.agro_panel.conditions.irrigation_alert'),
        deficit: t('weather.agro_panel.conditions.irrigation_deficit'),
        unknown: t('weather.agro_panel.conditions.unknown'),
      },
    };
    return labels[type]?.[value] || t('weather.agro_panel.conditions.unknown');
  };

  const loadWeatherData = async () => {
    const effectiveParcelId = selectedParcelId || parcelId;
    const codeToUse = selectedMunicipalityCode || municipalityCode;
    if (!codeToUse && !effectiveParcelId) return;

    setLoading(true);
    setError(null);

    try {
      const useParcelApi = !!(effectiveParcelId && effectiveParcelId.length > 0);
      let latest: any[] = [];
      let historicalObs: any[] = [];

      // When a parcel is selected, use the corrected parcel weather API
      if (useParcelApi) {
        try {
          const parcelWeather = await api.getParcelWeather(effectiveParcelId!, {
            source: 'OPEN-METEO',
            data_type: 'HISTORY',
            limit: 72,
          });
          if (parcelWeather?.observations?.length > 0) {
            latest = [parcelWeather.observations[0]];
            historicalObs = parcelWeather.observations;
          }
        } catch (err) {
          logger.warn('Parcel weather API failed for agro panel, falling back:', err);
        }
      }

      // Fallback: municipality-based query
      if (latest.length === 0) {
        latest = await api.getLatestWeatherObservations({
          municipality_code: codeToUse,
          source: 'OPEN-METEO',
          data_type: 'HISTORY',
        });
      }

      if (historicalObs.length === 0) {
        const threeDaysAgo = new Date();
        threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);
        const historical = await api.getWeatherObservations({
          municipality_code: codeToUse,
          source: 'OPEN-METEO',
          data_type: 'HISTORY',
          start_date: threeDaysAgo.toISOString().split('T')[0],
          limit: 100,
        });
        historicalObs = historical?.observations || [];
      }

      // No data available — show clean empty state
      if (latest.length === 0 && historicalObs.length === 0) {
        logger.debug('[WeatherAgroPanel] No weather data available', {
          latestCount: latest.length,
          historicalCount: historicalObs.length,
        });
        setCurrentWeather(null);
        setHistoricalWeather([]);
      } else {
        // Use DB data
        if (latest.length > 0) {
          setCurrentWeather(latest[0]);
        }
        setHistoricalWeather(historicalObs);
      }
    } catch (err: any) {
      logger.error('Error loading weather data:', err);
      const errorMessage = err.response?.data?.detail || err.message || t('weather.agro_panel.error');
      setError(errorMessage);
      // Clear data on error
      setCurrentWeather(null);
      setHistoricalWeather([]);
    } finally {
      setLoading(false);
    }
  };

  const loadParcelSensors = async () => {
    if (!parcelId) return;

    try {
      const sensors = await api.getSensors();
      // Filter sensors that might be related to this parcel
      // In a real implementation, you'd check parcel_sensors relationship
      const soilSensors = sensors.filter(s => 
        s.moisture && s.location
      );
      setParcelSensors(soilSensors as ParcelSensor[]);
    } catch (err) {
      logger.warn('Error loading parcel sensors:', err);
      // Continue without sensor data - will use platform weather
    }
  };

  const searchMunicipalities = async (term: string) => {
    if (term.length < 2) {
      setMunicipalities([]);
      return;
    }

    setSearchingMunicipalities(true);
    try {
      logger.debug('[WeatherAgroPanel] Searching municipalities with term:', term);
      const response = await api.searchMunicipalities(term);
      logger.debug('[WeatherAgroPanel] Search response:', response);
      const filtered = (response.municipalities || []).map((mun: any) => ({
        code: mun.ine_code || mun.code,
        name: mun.name,
        province: mun.province,
        fullName: mun.province ? `${mun.name} (${mun.province})` : mun.name,
      }));
      logger.debug('[WeatherAgroPanel] Filtered municipalities:', filtered);
      setMunicipalities(filtered);
    } catch (err: any) {
      logger.error('[WeatherAgroPanel] Error searching municipalities:', err);
      logger.error('[WeatherAgroPanel] Error details:', {
        message: err.message,
        response: err.response?.data,
        status: err.response?.status,
      });
      setMunicipalities([]);
      // Show error to user
      setError(`${t('weather.agro_panel.error')}: ${err.message}`);
    } finally {
      setSearchingMunicipalities(false);
    }
  };

  useEffect(() => {
    if (searchTerm) {
      const timeout = setTimeout(() => searchMunicipalities(searchTerm), 300);
      return () => clearTimeout(timeout);
    } else {
      setMunicipalities([]);
    }
  }, [searchTerm]);

  // Calculate spraying condition
  const getSprayingCondition = (): { condition: SprayingCondition; message: string; color: string } => {
    if (!currentWeather) {
      return { condition: 'unknown', message: t('weather.agro_panel.conditions.unknown'), color: 'gray' };
    }

    const windKmh = currentWeather.wind_speed_ms ? currentWeather.wind_speed_ms * 3.6 : 0;
    const deltaT = currentWeather.delta_t || 0;
    const precipProb = currentWeather.metadata?.precipitation_probability || 0;

    // 🟢 Verde (Óptimo): Viento < 15km/h AND Delta T entre 2 y 8
    if (windKmh < 15 && deltaT >= 2 && deltaT <= 8) {
      return {
        condition: 'optimal',
        message: t('weather.agro_panel.conditions.spraying_optimal'),
        color: 'green',
      };
    }

    // 🔴 Rojo (No tratar): Viento > 20km/h OR Delta T > 10 OR Probabilidad Lluvia > 50%
    if (windKmh > 20 || deltaT > 10 || precipProb > 50) {
      return {
        condition: 'not_suitable',
        message: t('weather.agro_panel.conditions.spraying_not_suitable'),
        color: 'red',
      };
    }

    // 🟡 Amarillo (Precaución): Viento 15-20km/h OR Delta T entre 8 y 10 (o < 2)
    if ((windKmh >= 15 && windKmh <= 20) || (deltaT >= 8 && deltaT <= 10) || deltaT < 2) {
      return {
        condition: 'caution',
        message: t('weather.agro_panel.conditions.spraying_caution'),
        color: 'yellow',
      };
    }

    return { condition: 'unknown', message: t('weather.agro_panel.conditions.evaluating'), color: 'gray' };
  };

  // Calculate workability condition (tempero)
  const getWorkabilityCondition = (): { condition: WorkabilityCondition; message: string; color: string; soilMoisture: number | null } => {
    // Priority: Use real sensor data if available, otherwise platform weather data
    let soilMoisture: number | null = null;

    if (parcelSensors.length > 0 && parcelSensors[0].moisture?.value !== undefined) {
      // Use real sensor data
      soilMoisture = parcelSensors[0].moisture.value;
    } else if (currentWeather?.soil_moisture_0_10cm !== undefined) {
      // Fallback to platform weather data
      soilMoisture = currentWeather.soil_moisture_0_10cm;
    }

    if (soilMoisture === null) {
      return {
        condition: 'unknown',
        message: t('weather.agro_panel.conditions.unknown'),
        color: 'gray',
        soilMoisture: null,
      };
    }

    // 🟢 Verde (En Tempero): Humedad entre 15% y 25%
    if (soilMoisture >= 15 && soilMoisture <= 25) {
      return {
        condition: 'optimal',
        message: t('weather.agro_panel.conditions.workability_optimal'),
        color: 'green',
        soilMoisture,
      };
    }

    // 🔴 Rojo (Barro/Compactación): Humedad > 25%
    if (soilMoisture > 25) {
      return {
        condition: 'too_wet',
        message: t('weather.agro_panel.conditions.workability_too_wet'),
        color: 'red',
        soilMoisture,
      };
    }

    // 🟡 Amarillo (Seco/Polvo): Humedad < 10%
    if (soilMoisture < 10) {
      return {
        condition: 'too_dry',
        message: t('weather.agro_panel.conditions.workability_too_dry'),
        color: 'yellow',
        soilMoisture,
      };
    }

    // Between 10-15% or 25-30%: caution zone
    return {
      condition: soilMoisture < 15 ? 'too_dry' : 'too_wet',
      message: t('weather.agro_panel.conditions.workability_caution'),
      color: 'yellow',
      soilMoisture,
    };
  };

  // Calculate irrigation condition (water balance)
  const getIrrigationCondition = (): { condition: IrrigationCondition; message: string; color: string; balance: number } => {
    if (historicalWeather.length === 0) {
      return {
        condition: 'unknown',
        message: t('weather.agro_panel.conditions.unknown'),
        color: 'gray',
        balance: 0,
      };
    }

    // Calculate accumulated precipitation and ET₀ for last 3 days
    const last3Days = historicalWeather
      .filter(obs => {
        const obsDate = new Date(obs.observed_at);
        const threeDaysAgo = new Date();
        threeDaysAgo.setDate(threeDaysAgo.getDate() - 3);
        return obsDate >= threeDaysAgo;
      })
      .slice(-72); // Last 72 hours (3 days * 24 hours)

    const totalPrecip = last3Days.reduce((sum, obs) => sum + (obs.precip_mm || 0), 0);
    const totalET0 = last3Days.reduce((sum, obs) => sum + (obs.eto_mm || 0), 0);
    const balance = totalPrecip - totalET0;

    // 🟢 Verde (Satisfecho): Balance > 0
    if (balance > 0) {
      return {
        condition: 'satisfied',
        message: t('weather.agro_panel.conditions.irrigation_satisfied'),
        color: 'green',
        balance: Math.round(balance * 10) / 10,
      };
    }

    // 🔴 Rojo (Déficit Hídrico): Balance < -5mm
    if (balance < -5) {
      return {
        condition: 'deficit',
        message: t('weather.agro_panel.conditions.irrigation_deficit'),
        color: 'red',
        balance: Math.round(balance * 10) / 10,
      };
    }

    // 🟡 Amarillo (Alerta): Balance entre 0 y -5mm
    return {
      condition: 'alert',
      message: t('weather.agro_panel.conditions.irrigation_alert'),
      color: 'yellow',
      balance: Math.round(balance * 10) / 10,
    };
  };

  // Use backend semaphores when available (parcel selected + API success), fall back to local
  const backendSemaphores = agroStatus?.semaphores;
  const spraying = backendSemaphores
    ? { condition: backendSemaphores.spraying as SprayingCondition, message: semaphoreLabel('spraying', backendSemaphores.spraying), color: semaphoreColor(backendSemaphores.spraying) }
    : getSprayingCondition();
  const workability = backendSemaphores
    ? { condition: backendSemaphores.workability as WorkabilityCondition, message: semaphoreLabel('workability', backendSemaphores.workability), color: semaphoreColor(backendSemaphores.workability), soilMoisture: null as number | null }
    : getWorkabilityCondition();
  const irrigation = backendSemaphores
    ? { condition: backendSemaphores.irrigation as IrrigationCondition, message: semaphoreLabel('irrigation', backendSemaphores.irrigation), color: semaphoreColor(backendSemaphores.irrigation), balance: agroStatus?.metrics?.water_balance ?? 0 }
    : getIrrigationCondition();

  const getStatusIcon = (color: string) => {
    switch (color) {
      case 'green':
        return <CheckCircle2 className="w-6 h-6 text-nkz-success" />;
      case 'yellow':
        return <AlertCircle className="w-6 h-6 text-nkz-warning" />;
      case 'red':
        return <XCircle className="w-6 h-6 text-nkz-error" />;
      default:
        return <AlertCircle className="w-6 h-6 text-nkz-muted" />;
    }
  };

  const getStatusBgColor = (color: string) => {
    switch (color) {
      case 'green':
        return 'bg-nkz-success-light border-green-200';
      case 'yellow':
        return 'bg-nkz-warning-light border-yellow-200';
      case 'red':
        return 'bg-nkz-error-light border-red-200';
      default:
        return 'bg-nkz-bg-secondary border-nkz-border';
    }
  };

  return (
    <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-green-600 to-green-700 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Sprout className="w-6 h-6 text-white" />
            <div>
              <h2 className="text-xl font-bold text-white">{t('weather.agro_panel.title')}</h2>
              <p className="text-sm text-green-100">
                {selectedParcelName || selectedMunicipalityName || municipalityName || t('weather.agro_panel.select_municipality')}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            {/* Parcel selector */}
            <div className="relative">
              <Button
                onClick={() => setShowParcelSearch(!showParcelSearch)}
                className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white text-sm flex items-center gap-2"
              >
                <MapPin className="w-4 h-4" />
                {selectedParcelName ? selectedParcelName.substring(0, 20) : t('weather.agro_panel.select_parcel')}
              </Button>
              {showParcelSearch && parcels.length > 0 && (
                <div className="absolute right-0 mt-1 w-64 max-h-64 overflow-y-auto bg-white rounded-lg shadow-lg z-50 border border-nkz-border">
                  {parcels.map((p) => (
                    <Button
                      key={p.id}
                      onClick={() => {
                        setSelectedParcelId(p.id);
                        setSelectedParcelName(p.name);
                        setShowParcelSearch(false);
                      }}
                      className={`w-full px-4 py-2 text-left text-sm hover:bg-nkz-success-light transition flex items-center gap-2 border-b border-gray-100 last:border-b-0 ${
                        p.id === selectedParcelId ? 'bg-nkz-success-light' : ''
                      }`}
                    >
                      <MapPin className="w-3 h-3 text-green-500 flex-shrink-0" />
                      <span className="truncate">{p.name}</span>
                    </Button>
                  ))}
                </div>
              )}
            </div>
            {/* Municipality search toggle */}
            <Button
              onClick={() => setShowMunicipalitySearch(!showMunicipalitySearch)}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white text-sm flex items-center gap-2"
              title={t('weather.agro_panel.search')}
            >
              <Search className="w-4 h-4" />
            </Button>
            <Button
              onClick={loadWeatherData}
              disabled={loading || (!selectedMunicipalityCode && !selectedParcelId)}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </div>

      {/* Municipality Search */}
      {showMunicipalitySearch && (
        <div className="p-4 bg-nkz-bg-secondary border-b">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-nkz-muted" />
            <Input
              type="text"
              value={searchTerm}
              onChange={(e: any) => setSearchTerm(e.target.value)}
              placeholder={t('weather.agro_panel.search_municipality_placeholder')}
              className="w-full pl-10 pr-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
            />
            {searchingMunicipalities && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-nkz-muted animate-spin" />
            )}
          </div>
          {municipalities.length > 0 ? (
            <div className="mt-2 max-h-64 overflow-y-auto border border-nkz-border rounded-lg bg-white shadow-lg">
              {municipalities.map((municipality) => (
                <Button
                  key={municipality.code}
                  onClick={() => {
                    const code = municipality.code;
                    const name = municipality.name;
                    
                    // Update internal state
                    setSelectedMunicipalityCode(code);
                    setSelectedMunicipalityName(name);
                    
                    if (onMunicipalitySelect) {
                      onMunicipalitySelect(code, name);
                    }
                    
                    setShowMunicipalitySearch(false);
                    setSearchTerm('');
                    setMunicipalities([]);
                    
                    // Clear old data - loadWeatherData will be triggered by useEffect
                    setCurrentWeather(null);
                    setHistoricalWeather([]);
                  }}
                  className="w-full px-4 py-2 text-left hover:bg-nkz-success-light transition flex items-center gap-2 border-b border-gray-100 last:border-b-0"
                >
                  <MapPin className="w-4 h-4 text-green-500 flex-shrink-0" />
                  <span className="text-sm font-medium text-gray-900">
                    {municipality.fullName || municipality.name}
                  </span>
                </Button>
              ))}
            </div>
          ) : searchTerm.length >= 2 ? (
            <div className="mt-2 p-4 text-center text-nkz-muted">
              <p className="text-sm">{t('weather.agro_panel.no_municipalities_found')}</p>
            </div>
          ) : null}
        </div>
      )}

      {/* Content */}
      <div className="p-6">
        {error && (
          <div className="mb-4 p-4 bg-nkz-error-light border border-red-200 rounded-lg flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-nkz-error flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-red-800 text-sm font-medium">{t('weather.agro_panel.error')}</p>
              <p className="text-nkz-error text-sm">{error}</p>
            </div>
          </div>
        )}

        {loading && !currentWeather ? (
          <div className="text-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-nkz-success mx-auto mb-4" />
            <p className="text-gray-600">{t('weather.agro_panel.loading_agro_data')}</p>
          </div>
        ) : currentWeather ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Panel A: Pulverización */}
            <div className={`rounded-xl p-5 border-2 ${getStatusBgColor(spraying.color)}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Wind className="w-5 h-5 text-gray-700" />
                  <h3 className="font-semibold text-gray-900">{t('weather.agro_panel.spraying')}</h3>
                </div>
                {getStatusIcon(spraying.color)}
              </div>
              <p className="text-sm text-gray-700 mb-3">{spraying.message}</p>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('weather.agro_panel.wind')}</span>
                  <span className="font-medium">
                    {currentWeather.wind_speed_ms 
                      ? `${Math.round(currentWeather.wind_speed_ms * 3.6)} km/h`
                      : 'N/A'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('weather.agro_panel.delta_t')}</span>
                  <span className="font-medium">
                    {currentWeather.delta_t !== undefined 
                      ? `${currentWeather.delta_t.toFixed(1)}°C`
                      : 'N/A'}
                  </span>
                </div>
                {currentWeather.metadata?.precipitation_probability !== undefined && (
                  <div className="flex justify-between">
                    <span className="text-gray-600">{t('weather.agro_panel.rain_prob')}</span>
                    <span className="font-medium">
                      {currentWeather.metadata.precipitation_probability}%
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* Panel B: Tempero */}
            <div className={`rounded-xl p-5 border-2 ${getStatusBgColor(workability.color)}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Droplets className="w-5 h-5 text-gray-700" />
                  <h3 className="font-semibold text-gray-900">{t('weather.agro_panel.workability')}</h3>
                </div>
                {getStatusIcon(workability.color)}
              </div>
              <p className="text-sm text-gray-700 mb-3">{workability.message}</p>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('weather.agro_panel.soil_moisture')}</span>
                  <span className="font-medium">
                    {workability.soilMoisture !== null
                      ? `${workability.soilMoisture.toFixed(1)}%`
                      : 'N/A'}
                  </span>
                </div>
                <div className="flex items-center gap-1 mt-2">
                  {parcelSensors.length > 0 ? (
                    <>
                      <CheckCircle2 className="w-3 h-3 text-nkz-success" />
                      <span className="text-xs text-gray-600">{t('weather.agro_panel.real_sensor_data')}</span>
                    </>
                  ) : (
                    <>
                      <Cloud className="w-3 h-3 text-nkz-info" />
                      <span className="text-xs text-gray-600">{t('weather.agro_panel.platform_data')}</span>
                    </>
                  )}
                </div>
                {agroStatus?.soil?.texture_applied && agroStatus.soil.texture_class && (
                  <div className="flex items-center gap-1 mt-1">
                    <Database className="w-3 h-3 text-amber-600" />
                    <span className="text-xs text-nkz-muted">
                      {agroStatus.soil.texture_class}
                      {agroStatus.soil.source && ` · ${agroStatus.soil.source}`}
                    </span>
                  </div>
                )}
              </div>
            </div>

            {/* Panel C: Riego */}
            <div className={`rounded-xl p-5 border-2 ${getStatusBgColor(irrigation.color)}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Thermometer className="w-5 h-5 text-gray-700" />
                  <h3 className="font-semibold text-gray-900">{t('weather.agro_panel.irrigation')}</h3>
                </div>
                {getStatusIcon(irrigation.color)}
              </div>
              <p className="text-sm text-gray-700 mb-3">{irrigation.message}</p>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-600">{t('weather.agro_panel.balance_3_days')}</span>
                  <span className={`font-medium ${irrigation.balance < 0 ? 'text-nkz-error' : 'text-nkz-success'}`}>
                    {irrigation.balance > 0 ? '+' : ''}{irrigation.balance.toFixed(1)} mm
                  </span>
                </div>
                <div className="text-xs text-nkz-muted mt-2">
                  {t('weather.agro_panel.precip_eto_accumulated')}
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-center py-12">
            <Sprout className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-gray-600 mb-4">{t('weather.agro_panel.no_data_available')}</p>
            <Button
              onClick={() => setShowMunicipalitySearch(true)}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition"
            >
              {t('weather.agro_panel.select_municipality_button')}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
};

