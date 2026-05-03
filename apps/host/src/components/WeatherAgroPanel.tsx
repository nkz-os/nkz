// =============================================================================
// Weather Agro Panel Component - Triple Panel Agronomic Dashboard
// =============================================================================
// Widget para mostrar información agronómica crítica: Pulverización, Tempero, Riego
// =============================================================================

import React, { useState, useEffect } from 'react';
import { 
  Cloud, 
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
import { useI18n } from '@/context/I18nContext';
import { useTenantMunicipality } from '@/hooks/useTenantMunicipality';
import { logger } from '@/utils/logger';

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

interface WeatherAgroPanelProps {
  municipalityCode?: string;
  municipalityName?: string;
  parcelId?: string; // Optional: if provided, use parcel-specific data
  onMunicipalitySelect?: (code: string, name: string) => void;
}

type SprayingCondition = 'optimal' | 'caution' | 'not_suitable' | 'unknown';
type WorkabilityCondition = 'optimal' | 'too_wet' | 'too_dry' | 'unknown';
type IrrigationCondition = 'satisfied' | 'alert' | 'deficit' | 'unknown';

export const WeatherAgroPanel: React.FC<WeatherAgroPanelProps> = ({
  municipalityCode,
  municipalityName,
  parcelId,
  onMunicipalitySelect,
}) => {
  const { t: _t } = useI18n();

  const { municipality: tenantMunicipality, loading: _loadingTenantMunicipality } = useTenantMunicipality();
  
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
    municipalityCode || savedMunicipality.code || tenantMunicipality?.code
  );
  const [selectedMunicipalityName, setSelectedMunicipalityName] = useState<string | undefined>(
    municipalityName || savedMunicipality.name || tenantMunicipality?.name
  );
  const [currentWeather, setCurrentWeather] = useState<WeatherObservation | null>(null);
  const [historicalWeather, setHistoricalWeather] = useState<WeatherObservation[]>([]);
  const [parcelSensors, setParcelSensors] = useState<ParcelSensor[]>([]);
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
    } else if (tenantMunicipality && !selectedMunicipalityCode) {
      // Auto-set from tenant if no prop provided
      setSelectedMunicipalityCode(tenantMunicipality.code);
    }
    
    if (municipalityName) {
      setSelectedMunicipalityName(municipalityName);
    } else if (tenantMunicipality && !selectedMunicipalityName) {
      // Auto-set from tenant if no prop provided
      setSelectedMunicipalityName(tenantMunicipality.name);
    }
  }, [municipalityCode, municipalityName, tenantMunicipality]);

  // Load weather data
  useEffect(() => {
    if (selectedMunicipalityCode) {
      loadWeatherData();
    }
  }, [selectedMunicipalityCode, parcelId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Load parcel sensors if parcelId provided
  useEffect(() => {
    if (parcelId) {
      loadParcelSensors();
    }
  }, [parcelId]);

  const loadWeatherData = async () => {
    const codeToUse = selectedMunicipalityCode || municipalityCode;
    if (!codeToUse) return;

    setLoading(true);
    setError(null);

    try {
      const useParcelApi = !!(parcelId && parcelId.length > 0);
      let latest: any[] = [];
      let historicalObs: any[] = [];

      // When a parcel is selected, use the corrected parcel weather API
      if (useParcelApi) {
        try {
          const parcelWeather = await api.getParcelWeather(parcelId!, {
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
      const errorMessage = err.response?.data?.detail || err.message || 'Error cargando datos meteorológicos';
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
      setError(`Error buscando municipios: ${err.message || 'Error desconocido'}`);
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
      return { condition: 'unknown', message: 'Sin datos', color: 'gray' };
    }

    const windKmh = currentWeather.wind_speed_ms ? currentWeather.wind_speed_ms * 3.6 : 0;
    const deltaT = currentWeather.delta_t || 0;
    const precipProb = currentWeather.metadata?.precipitation_probability || 0;

    // 🟢 Verde (Óptimo): Viento < 15km/h AND Delta T entre 2 y 8
    if (windKmh < 15 && deltaT >= 2 && deltaT <= 8) {
      return {
        condition: 'optimal',
        message: 'Condiciones óptimas para pulverización',
        color: 'green',
      };
    }

    // 🔴 Rojo (No tratar): Viento > 20km/h OR Delta T > 10 OR Probabilidad Lluvia > 50%
    if (windKmh > 20 || deltaT > 10 || precipProb > 50) {
      return {
        condition: 'not_suitable',
        message: 'No tratar - Condiciones desfavorables',
        color: 'red',
      };
    }

    // 🟡 Amarillo (Precaución): Viento 15-20km/h OR Delta T entre 8 y 10 (o < 2)
    if ((windKmh >= 15 && windKmh <= 20) || (deltaT >= 8 && deltaT <= 10) || deltaT < 2) {
      return {
        condition: 'caution',
        message: 'Precaución - Condiciones marginales',
        color: 'yellow',
      };
    }

    return { condition: 'unknown', message: 'Evaluar condiciones', color: 'gray' };
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
        message: 'Sin datos de humedad de suelo',
        color: 'gray',
        soilMoisture: null,
      };
    }

    // 🟢 Verde (En Tempero): Humedad entre 15% y 25%
    if (soilMoisture >= 15 && soilMoisture <= 25) {
      return {
        condition: 'optimal',
        message: 'Suelo apto para labor',
        color: 'green',
        soilMoisture,
      };
    }

    // 🔴 Rojo (Barro/Compactación): Humedad > 25%
    if (soilMoisture > 25) {
      return {
        condition: 'too_wet',
        message: 'Riesgo de compactación - Suelo muy húmedo',
        color: 'red',
        soilMoisture,
      };
    }

    // 🟡 Amarillo (Seco/Polvo): Humedad < 10%
    if (soilMoisture < 10) {
      return {
        condition: 'too_dry',
        message: 'Suelo muy seco - Considerar riego',
        color: 'yellow',
        soilMoisture,
      };
    }

    // Between 10-15% or 25-30%: caution zone
    return {
      condition: soilMoisture < 15 ? 'too_dry' : 'too_wet',
      message: 'Condiciones marginales',
      color: 'yellow',
      soilMoisture,
    };
  };

  // Calculate irrigation condition (water balance)
  const getIrrigationCondition = (): { condition: IrrigationCondition; message: string; color: string; balance: number } => {
    if (historicalWeather.length === 0) {
      return {
        condition: 'unknown',
        message: 'Sin datos históricos',
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
        message: 'Suelo con agua suficiente',
        color: 'green',
        balance: Math.round(balance * 10) / 10,
      };
    }

    // 🔴 Rojo (Déficit Hídrico): Balance < -5mm
    if (balance < -5) {
      return {
        condition: 'deficit',
        message: 'Necesita riego urgente',
        color: 'red',
        balance: Math.round(balance * 10) / 10,
      };
    }

    // 🟡 Amarillo (Alerta): Balance entre 0 y -5mm
    return {
      condition: 'alert',
      message: 'Alerta - Déficit hídrico moderado',
      color: 'yellow',
      balance: Math.round(balance * 10) / 10,
    };
  };

  const spraying = getSprayingCondition();
  const workability = getWorkabilityCondition();
  const irrigation = getIrrigationCondition();

  const getStatusIcon = (color: string) => {
    switch (color) {
      case 'green':
        return <CheckCircle2 className="w-6 h-6 text-green-600" />;
      case 'yellow':
        return <AlertCircle className="w-6 h-6 text-yellow-600" />;
      case 'red':
        return <XCircle className="w-6 h-6 text-red-600" />;
      default:
        return <AlertCircle className="w-6 h-6 text-gray-400" />;
    }
  };

  const getStatusBgColor = (color: string) => {
    switch (color) {
      case 'green':
        return 'bg-green-50 border-green-200';
      case 'yellow':
        return 'bg-yellow-50 border-yellow-200';
      case 'red':
        return 'bg-red-50 border-red-200';
      default:
        return 'bg-gray-50 border-gray-200';
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
              <h2 className="text-xl font-bold text-white">Panel Agronómico</h2>
              <p className="text-sm text-green-100">
                {selectedMunicipalityName || municipalityName || 'Selecciona municipio'}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowMunicipalitySearch(!showMunicipalitySearch)}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white text-sm flex items-center gap-2"
            >
              <Search className="w-4 h-4" />
              {municipalityName ? 'Cambiar' : 'Buscar'}
            </button>
            <button
              onClick={loadWeatherData}
              disabled={loading || !selectedMunicipalityCode}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>
      </div>

      {/* Municipality Search */}
      {showMunicipalitySearch && (
        <div className="p-4 bg-gray-50 border-b">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Buscar municipio..."
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-green-500 focus:border-transparent"
            />
            {searchingMunicipalities && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 animate-spin" />
            )}
          </div>
          {municipalities.length > 0 ? (
            <div className="mt-2 max-h-64 overflow-y-auto border border-gray-200 rounded-lg bg-white shadow-lg">
              {municipalities.map((municipality) => (
                <button
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
                  className="w-full px-4 py-2 text-left hover:bg-green-50 transition flex items-center gap-2 border-b border-gray-100 last:border-b-0"
                >
                  <MapPin className="w-4 h-4 text-green-500 flex-shrink-0" />
                  <span className="text-sm font-medium text-gray-900">
                    {municipality.fullName || municipality.name}
                  </span>
                </button>
              ))}
            </div>
          ) : searchTerm.length >= 2 ? (
            <div className="mt-2 p-4 text-center text-gray-500">
              <p className="text-sm">No se encontraron municipios</p>
            </div>
          ) : null}
        </div>
      )}

      {/* Content */}
      <div className="p-6">
        {error && (
          <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <p className="text-red-800 text-sm font-medium">Error</p>
              <p className="text-red-700 text-sm">{error}</p>
            </div>
          </div>
        )}

        {loading && !currentWeather ? (
          <div className="text-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-green-600 mx-auto mb-4" />
            <p className="text-gray-600">Cargando datos agronómicos...</p>
          </div>
        ) : currentWeather ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Panel A: Pulverización */}
            <div className={`rounded-xl p-5 border-2 ${getStatusBgColor(spraying.color)}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Wind className="w-5 h-5 text-gray-700" />
                  <h3 className="font-semibold text-gray-900">Pulverización</h3>
                </div>
                {getStatusIcon(spraying.color)}
              </div>
              <p className="text-sm text-gray-700 mb-3">{spraying.message}</p>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-600">Viento:</span>
                  <span className="font-medium">
                    {currentWeather.wind_speed_ms 
                      ? `${Math.round(currentWeather.wind_speed_ms * 3.6)} km/h`
                      : 'N/A'}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-gray-600">Delta T:</span>
                  <span className="font-medium">
                    {currentWeather.delta_t !== undefined 
                      ? `${currentWeather.delta_t.toFixed(1)}°C`
                      : 'N/A'}
                  </span>
                </div>
                {currentWeather.metadata?.precipitation_probability !== undefined && (
                  <div className="flex justify-between">
                    <span className="text-gray-600">Prob. Lluvia:</span>
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
                  <h3 className="font-semibold text-gray-900">Tempero</h3>
                </div>
                {getStatusIcon(workability.color)}
              </div>
              <p className="text-sm text-gray-700 mb-3">{workability.message}</p>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-600">Humedad suelo:</span>
                  <span className="font-medium">
                    {workability.soilMoisture !== null
                      ? `${workability.soilMoisture.toFixed(1)}%`
                      : 'N/A'}
                  </span>
                </div>
                <div className="flex items-center gap-1 mt-2">
                  {parcelSensors.length > 0 ? (
                    <>
                      <CheckCircle2 className="w-3 h-3 text-green-600" />
                      <span className="text-xs text-gray-600">Datos de sensor real</span>
                    </>
                  ) : (
                    <>
                      <Cloud className="w-3 h-3 text-blue-600" />
                      <span className="text-xs text-gray-600">Datos de la plataforma</span>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Panel C: Riego */}
            <div className={`rounded-xl p-5 border-2 ${getStatusBgColor(irrigation.color)}`}>
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Thermometer className="w-5 h-5 text-gray-700" />
                  <h3 className="font-semibold text-gray-900">Riego</h3>
                </div>
                {getStatusIcon(irrigation.color)}
              </div>
              <p className="text-sm text-gray-700 mb-3">{irrigation.message}</p>
              <div className="space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-gray-600">Balance (3 días):</span>
                  <span className={`font-medium ${irrigation.balance < 0 ? 'text-red-600' : 'text-green-600'}`}>
                    {irrigation.balance > 0 ? '+' : ''}{irrigation.balance.toFixed(1)} mm
                  </span>
                </div>
                <div className="text-xs text-gray-500 mt-2">
                  Precip. - ET₀ acumulada
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-center py-12">
            <Sprout className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-gray-600 mb-4">No hay datos disponibles</p>
            <button
              onClick={() => setShowMunicipalitySearch(true)}
              className="px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition"
            >
              Seleccionar municipio
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

