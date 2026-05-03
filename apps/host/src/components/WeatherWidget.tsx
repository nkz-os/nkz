// =============================================================================
// Weather Widget Component - AEMET Integration
// =============================================================================
// Widget para mostrar información meteorológica de AEMET en el dashboard
// =============================================================================

import React, { useState, useEffect } from 'react';
import { Cloud, Thermometer, Droplets, Wind, MapPin, Search, Loader2, AlertCircle, RefreshCw } from 'lucide-react';
import api from '@/services/api';
import { useI18n } from '@/context/I18nContext';
import { useTenantMunicipality } from '@/hooks/useTenantMunicipality';
import { logger } from '@/utils/logger';

interface WeatherData {
  observed_at: string;
  temp_avg?: number;
  temp_min?: number;
  temp_max?: number;
  humidity_avg?: number;
  precip_mm?: number;
  solar_rad_w_m2?: number;
  eto_mm?: number;
  wind_speed_ms?: number;
  wind_direction_deg?: number;
  pressure_hpa?: number;
  gdd_accumulated?: number;
  viento?: {
    direccion: string;
    velocidad: number;
  };
}

interface ForecastData {
  fecha: string;
  t_maxima: number;
  t_minima: number;
  estado_cielo?: string;
  precipitacion_proba?: number;
}

interface WeatherWidgetProps {
  municipalityCode?: string;
  municipalityName?: string;
  latitude?: number;
  longitude?: number;
  parcelId?: string;
  onMunicipalitySelect?: (code: string, name: string) => void;
}

export const WeatherWidget: React.FC<WeatherWidgetProps> = ({
  municipalityCode,
  municipalityName,
  latitude: _latitude,
  longitude: _longitude,
  parcelId,
  onMunicipalitySelect,
}) => {
  const { t } = useI18n();
  
  // Auto-detect municipality from tenant if not provided
  const { municipality: tenantMunicipality } = useTenantMunicipality();
  
  const [weatherData, setWeatherData] = useState<WeatherData | null>(null);
  const [forecast, setForecast] = useState<ForecastData[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downscaling, setDownscaling] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Array<{
    alert_type: string;
    alert_category: string;
    effective_from: string;
    effective_to: string;
    description: string;
  }>>([]);
  const [showMunicipalitySearch, setShowMunicipalitySearch] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [municipalities, setMunicipalities] = useState<Array<{ code: string; name: string; province?: string; fullName?: string }>>([]);
  const [searchingMunicipalities, setSearchingMunicipalities] = useState(false);
  const [selectedMunicipalityName, setSelectedMunicipalityName] = useState<string | null>(
    municipalityName || tenantMunicipality?.name || null
  );
  const [selectedMunicipalityProvince, setSelectedMunicipalityProvince] = useState<string | null>(null);
  
  // Determine which municipality code to use (priority: prop > tenant)
  const effectiveMunicipalityCode = municipalityCode || tenantMunicipality?.code;

  // Load weather data
  useEffect(() => {
    if (effectiveMunicipalityCode) {
      loadWeatherByMunicipality(
        effectiveMunicipalityCode,
        municipalityName || tenantMunicipality?.name,
        tenantMunicipality?.province
      );
    } else {
      // Try to get primary location from tenant weather locations
      loadWeatherFromPrimaryLocation();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveMunicipalityCode, municipalityCode, tenantMunicipality]);

  const loadWeatherFromPrimaryLocation = async () => {
    setLoading(true);
    setError(null);

    try {
      // Get tenant weather locations and use primary one
      const locations = await api.getWeatherLocations();
      const primaryLocation = locations.find((loc: any) => loc.is_primary) || locations[0];
      
      if (primaryLocation) {
        await loadWeatherByMunicipality(primaryLocation.municipality_code, primaryLocation.municipality_name);
      } else {
        // No error if no location - just show empty state
        setError(null);
        setWeatherData(null);
        setForecast([]);
      }
    } catch (err: any) {
      logger.error('Error loading weather from primary location:', err);
      // Don't show error if it's just that there's no location configured
      if (err.response?.status !== 404) {
      setError(err.message || t('weather.error_loading'));
      } else {
        setError(null);
        setWeatherData(null);
        setForecast([]);
      }
    } finally {
      setLoading(false);
    }
  };

  const loadWeatherByMunicipality = async (code?: string, name?: string, province?: string) => {
    const targetCode = code || municipalityCode;
    if (!targetCode) return;

    setLoading(true);
    setError(null);

    try {
      // When a parcel is selected, use the corrected parcel weather API
      const useParcelApi = !!(parcelId && parcelId.length > 0);
      let observations: any[] = [];
      let forecastObs: any[] = [];
      let parcelDownscaling: string | null = null;

      if (useParcelApi) {
        try {
          const parcelWeather = await api.getParcelWeather(parcelId!, {
            source: 'OPEN-METEO',
            data_type: 'HISTORY',
            limit: 1,
          });
          if (parcelWeather && parcelWeather.observations?.length > 0) {
            observations = parcelWeather.observations;
            parcelDownscaling = parcelWeather.downscaling || null;

            // Also fetch forecast for this parcel
            const parcelForecast = await api.getParcelWeather(parcelId!, {
              source: 'OPEN-METEO',
              data_type: 'FORECAST',
              limit: 96,
            });
            forecastObs = parcelForecast?.observations || [];
          }
          setDownscaling(parcelDownscaling);
        } catch (err) {
          logger.warn('Parcel weather API failed, falling back to municipality:', err);
          // Fall through to municipality-based query
        }
      }

      // Municipality-based query (fallback or primary)
      if (observations.length === 0) {
        setDownscaling(null);
        observations = await api.getLatestWeatherObservations({
          municipality_code: targetCode,
          source: 'OPEN-METEO',
          data_type: 'HISTORY',
        });
      }

      // Get forecast data if not already loaded from parcel API
      let forecastData: { observations?: any[]; count?: number } | null = null;
      if (forecastObs.length === 0) {
        try {
          forecastData = await api.getWeatherObservations({
            municipality_code: targetCode,
            source: 'OPEN-METEO',
            data_type: 'FORECAST',
            limit: 200,
          });
        } catch (forecastErr) {
          logger.warn('Error fetching forecast data:', forecastErr);
          // Continue without forecast - not critical
        }
      }

      // No data in DB for this municipality — show clean empty state
      if (observations.length === 0 && (!forecastData || forecastData.observations?.length === 0)) {
        logger.debug('[WeatherWidget] No weather data available for municipality', {
          observationsCount: observations.length,
          forecastDataCount: forecastData?.observations?.length || 0
        });
        setWeatherData(null);
        setForecast([]);
        setLoading(false);
        return;
      }

      // Process data from DB
      if (observations.length > 0) {
        const latest = observations[0];
        if (name) setSelectedMunicipalityName(name);
        if (province) setSelectedMunicipalityProvince(province || null);
        // Transform to widget format
        setWeatherData({
          observed_at: latest.observed_at,
          temp_avg: latest.temp_avg,
          temp_min: latest.temp_min,
          temp_max: latest.temp_max,
          humidity_avg: latest.humidity_avg,
          precip_mm: latest.precip_mm,
          pressure_hpa: latest.pressure_hpa,
          viento: {
            direccion: latest.wind_direction_deg ? `${latest.wind_direction_deg}°` : 'N',
            velocidad: latest.wind_speed_ms ? Math.round(latest.wind_speed_ms * 3.6) : 0, // Convert m/s to km/h
          },
        });
      }

      // Process forecast from parcel API or DB
      const forecastObservations = forecastObs.length > 0
        ? forecastObs
        : (forecastData?.observations || []);

      let forecastProcessed = false;
      if (forecastObservations.length > 0) {
        // Transform forecast to widget format
        // Group hourly observations by date and calculate daily min/max
        const nowForDB = new Date();
        const todayForDB = new Date(nowForDB.getFullYear(), nowForDB.getMonth(), nowForDB.getDate());
        const maxDate = new Date(todayForDB);
        maxDate.setDate(maxDate.getDate() + 5);

        const dailyData = new Map<string, {
          temps: number[];
          precip: number[];
          weatherCodes: string[];
        }>();

        forecastObservations.forEach((obs: any) => {
          if (!obs.observed_at) return;
          
          const obsDate = new Date(obs.observed_at);
          if (isNaN(obsDate.getTime())) {
            logger.warn('[WeatherWidget] Invalid date in forecast observation:', obs.observed_at);
            return;
          }
          
          // Get date only (ignore time)
          const obsDateOnly = new Date(obsDate.getFullYear(), obsDate.getMonth(), obsDate.getDate());
          const dateKey = obsDateOnly.toISOString().split('T')[0];
          
          // Only include today and future dates (exclude yesterday)
          // Compare dates properly to avoid timezone issues
          const todayTime = todayForDB.getTime();
          const obsTime = obsDateOnly.getTime();
          if (obsTime >= todayTime && obsTime <= maxDate.getTime()) {
            if (!dailyData.has(dateKey)) {
              dailyData.set(dateKey, { temps: [], precip: [], weatherCodes: [] });
            }
            
            const dayData = dailyData.get(dateKey)!;
            
            // Collect temperatures (use temp_avg if temp_min/max not available)
            if (obs.temp_avg !== null && obs.temp_avg !== undefined) {
              dayData.temps.push(obs.temp_avg);
            }
            if (obs.temp_min !== null && obs.temp_min !== undefined) {
              dayData.temps.push(obs.temp_min);
            }
            if (obs.temp_max !== null && obs.temp_max !== undefined) {
              dayData.temps.push(obs.temp_max);
            }
            
            // Collect precipitation
            if (obs.precip_mm !== null && obs.precip_mm !== undefined && obs.precip_mm > 0) {
              dayData.precip.push(obs.precip_mm);
            }
            
            // Collect weather codes
            if (obs.metadata?.weather_code) {
              dayData.weatherCodes.push(obs.metadata.weather_code);
            }
          }
        });
        
        // Transform to forecast format (calculate min/max from collected temps)
        const nowForDBTransform = new Date();
        const todayForDBTransform = new Date(nowForDBTransform.getFullYear(), nowForDBTransform.getMonth(), nowForDBTransform.getDate());
        const todayTime = todayForDBTransform.getTime();
        
        const forecastTransformed = Array.from(dailyData.entries())
          .map(([dateKey, dayData]) => {
            const dateOnly = new Date(dateKey + 'T00:00:00');
            const temps = dayData.temps.filter(t => t !== null && t !== undefined);
            const t_maxima = temps.length > 0 ? Math.max(...temps) : null;
            const t_minima = temps.length > 0 ? Math.min(...temps) : null;
            const totalPrecip = dayData.precip.reduce((sum, p) => sum + p, 0);
            const hasPrecip = totalPrecip > 0;
            
            return {
              fecha: dateKey,
              fechaDate: new Date(dateOnly.getFullYear(), dateOnly.getMonth(), dateOnly.getDate()),
              t_maxima: t_maxima || 0,
              t_minima: t_minima || 0,
              estado_cielo: hasPrecip ? 'Lluvia' : 'Despejado',
              precipitacion_proba: hasPrecip ? Math.min(100, totalPrecip * 10) : 0,
            };
          })
          .filter(item => item.fechaDate.getTime() >= todayTime) // Exclude yesterday explicitly
          .sort((a, b) => new Date(a.fecha).getTime() - new Date(b.fecha).getTime())
          .slice(0, 5); // Get first 5 days
        
        if (forecastTransformed.length >= 5) {
          setForecast(forecastTransformed);
          forecastProcessed = true;
          logger.debug('[WeatherWidget] Forecast successfully processed from DB:', forecastTransformed.length, 'days');
        } else if (forecastTransformed.length > 0) {
          logger.debug('[WeatherWidget] Forecast from DB has fewer than 5 days (', forecastTransformed.length, ')');
          setForecast(forecastTransformed);
        } else {
          logger.warn(`[WeatherWidget] Forecast empty. Total observations: ${forecastObservations.length}`);
          logger.debug('[WeatherWidget] Daily data keys:', Array.from(dailyData.keys()));
        }
      }

      // Forecast not in DB — keep whatever partial forecast we got, if any
      if (!forecastProcessed && forecast.length === 0) {
        logger.debug('[WeatherWidget] No forecast data available for municipality');
      }

      // Update municipality name if provided
      if (name && onMunicipalitySelect) {
        onMunicipalitySelect(targetCode, name);
      }

      // Load active weather alerts for this municipality
      try {
        const alertData = await api.getWeatherAlerts({ municipality_code: targetCode });
        setAlerts(alertData.alerts || []);
      } catch (alertErr) {
        logger.warn('Error fetching weather alerts:', alertErr);
        setAlerts([]);
      }
    } catch (err: any) {
      logger.error('Error loading weather by municipality:', err);
      const errorMessage = err.response?.data?.detail || err.message || t('weather.error_loading');
      setError(errorMessage);
      // Clear data on error
      setWeatherData(null);
      setForecast([]);
    } finally {
      setLoading(false);
    }
  };

  const searchMunicipalities = async (term: string) => {
    if (term.length < 2) {
      setMunicipalities([]);
      return;
    }

    setSearchingMunicipalities(true);
    try {
      logger.debug('[WeatherWidget] Searching municipalities with term:', term);
      // Search in catalog using API endpoint (searches AEMET/INE catalog)
      const response = await api.searchMunicipalities(term);
      logger.debug('[WeatherWidget] Search response:', response);
      const municipalities = response.municipalities || [];
      
      const filtered = municipalities.map((mun: any) => ({
        code: mun.ine_code || mun.code,
        name: mun.name,
        province: mun.province,
        fullName: mun.province ? `${mun.name} (${mun.province})` : mun.name,
      }));
      
      logger.debug('[WeatherWidget] Filtered municipalities:', filtered);
      setMunicipalities(filtered);
    } catch (err: any) {
      logger.error('[WeatherWidget] Error searching municipalities:', err);
      logger.error('[WeatherWidget] Error details:', {
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

  const handleMunicipalitySelect = async (code: string, name: string, province?: string) => {
    if (onMunicipalitySelect) {
      onMunicipalitySelect(code, name);
    }
    setSelectedMunicipalityName(name);
    setSelectedMunicipalityProvince(province || null);
    setShowMunicipalitySearch(false);
    setSearchTerm('');
    setMunicipalities([]);
    
    // Ensure location exists in tenant_weather_locations
    try {
      const locations = await api.getWeatherLocations();
      const locationExists = locations.some((loc: any) => loc.municipality_code === code);
      
      if (!locationExists) {
        // Create location automatically if it doesn't exist
        await api.createWeatherLocation({
          municipality_code: code,
          is_primary: locations.length === 0, // Set as primary if it's the first one
          label: name,
        });
      }
    } catch (err) {
      logger.warn('Error ensuring weather location exists:', err);
      // Continue anyway - the widget can still work without the location in the DB
    }
    
    // Load weather for selected municipality
    await loadWeatherByMunicipality(code, name, province);
  };

  return (
    <div className="bg-white rounded-2xl shadow-lg overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-500 to-blue-600 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Cloud className="w-6 h-6 text-white" />
            <div>
              <h2 className="text-xl font-bold text-white">{t('weather.widget_title')}</h2>
              <p className="text-sm text-blue-100">
                {selectedMunicipalityName || municipalityName || tenantMunicipality?.name || t('weather.widget_subtitle_select')}
                {selectedMunicipalityProvince || tenantMunicipality?.province ? ` (${selectedMunicipalityProvince || tenantMunicipality?.province})` : ''}
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setShowMunicipalitySearch(!showMunicipalitySearch)}
              className="px-3 py-2 bg-white/20 hover:bg-white/30 rounded-lg transition text-white text-sm flex items-center gap-2"
            >
              <Search className="w-4 h-4" />
              {municipalityName ? t('weather.change_municipality') : t('weather.search_municipality')}
            </button>
            <button
              onClick={() => effectiveMunicipalityCode && loadWeatherByMunicipality(
                effectiveMunicipalityCode,
                municipalityName || tenantMunicipality?.name,
                tenantMunicipality?.province
              )}
              disabled={loading || !effectiveMunicipalityCode}
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
              placeholder={t('weather.search_placeholder')}
              className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            {searchingMunicipalities && (
              <Loader2 className="absolute right-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400 animate-spin" />
            )}
          </div>
          {searchingMunicipalities ? (
            <div className="mt-2 p-4 text-center text-gray-500">
              <Loader2 className="w-5 h-5 animate-spin mx-auto mb-2" />
              <p className="text-sm">Buscando municipios...</p>
            </div>
          ) : municipalities.length > 0 ? (
            <div className="mt-2 max-h-64 overflow-y-auto border border-gray-200 rounded-lg bg-white shadow-lg">
              {municipalities.map((municipality) => (
                <button
                  key={municipality.code}
                  onClick={() => handleMunicipalitySelect(municipality.code, municipality.name, municipality.province)}
                  className="w-full px-4 py-2 text-left hover:bg-blue-50 transition flex items-center gap-2 border-b border-gray-100 last:border-b-0"
                >
                  <MapPin className="w-4 h-4 text-blue-500 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-medium text-gray-900 block truncate">
                      {municipality.fullName || municipality.name}
                    </span>
                    {municipality.province && municipality.name !== municipality.province && (
                      <span className="text-xs text-gray-500">{municipality.province}</span>
                    )}
                  </div>
                </button>
              ))}
            </div>
          ) : searchTerm.length >= 2 ? (
            <div className="mt-2 p-3 text-sm text-gray-500 text-center bg-gray-50 rounded-lg">
              No se encontraron municipios. Intenta con otro término de búsqueda.
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

        {/* AEMET Weather Alerts */}
        {alerts.length > 0 && (
          <div className="mb-4 space-y-2">
            {alerts.map((alert, idx) => {
              const alertColors: Record<string, string> = {
                RED: 'bg-red-600 border-red-700',
                ORANGE: 'bg-orange-500 border-orange-600',
                YELLOW: 'bg-yellow-500 border-yellow-600',
              };
              const alertLabels: Record<string, string> = {
                RED: 'Alerta Roja',
                ORANGE: 'Alerta Naranja',
                YELLOW: 'Alerta Amarilla',
              };
              const bg = alertColors[alert.alert_type] || alertColors.YELLOW;
              const label = alertLabels[alert.alert_type] || alert.alert_type;
              return (
                <div
                  key={idx}
                  className={`${bg} text-white px-4 py-3 rounded-lg border flex items-start gap-3`}
                >
                  <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="font-semibold text-sm">
                      {label} — {alert.alert_category || 'Meteorológica'}
                    </p>
                    {alert.description && (
                      <p className="text-xs mt-1 opacity-90 line-clamp-2">
                        {alert.description}
                      </p>
                    )}
                    <p className="text-xs mt-1 opacity-75">
                      {new Date(alert.effective_from).toLocaleString('es-ES', {
                        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                      })}
                      {alert.effective_to && ` — ${new Date(alert.effective_to).toLocaleString('es-ES', {
                        day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                      })}`}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {loading && !weatherData ? (
          <div className="text-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-blue-600 mx-auto mb-4" />
              <p className="text-gray-600">{t('weather.loading')}</p>
          </div>
        ) : weatherData ? (
          <>
            {/* Current Weather */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              <div className="bg-gradient-to-br from-orange-50 to-orange-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Thermometer className="w-5 h-5 text-orange-600" />
                  <span className="text-xs font-medium text-orange-700">{t('weather.temperature')}</span>
                </div>
                <p className="text-2xl font-bold text-orange-900">{weatherData.temp_avg?.toFixed(1) || 'N/A'}°C</p>
              </div>

              <div className="bg-gradient-to-br from-blue-50 to-blue-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Droplets className="w-5 h-5 text-blue-600" />
                  <span className="text-xs font-medium text-blue-700">{t('weather.humidity')}</span>
                </div>
                <p className="text-2xl font-bold text-blue-900">{weatherData.humidity_avg?.toFixed(0) || 'N/A'}%</p>
              </div>

              <div className="bg-gradient-to-br from-gray-50 to-gray-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Wind className="w-5 h-5 text-gray-600" />
                  <span className="text-xs font-medium text-gray-700">{t('weather.wind')}</span>
                </div>
                <p className="text-2xl font-bold text-gray-900">{weatherData.viento?.velocidad || weatherData.wind_speed_ms ? Math.round((weatherData.wind_speed_ms || 0) * 3.6) : 'N/A'} km/h</p>
                <p className="text-xs text-gray-600 mt-1">{weatherData.viento?.direccion || (weatherData.wind_direction_deg ? `${weatherData.wind_direction_deg}°` : 'N/A')}</p>
              </div>

              <div className="bg-gradient-to-br from-purple-50 to-purple-100 rounded-xl p-4">
                <div className="flex items-center gap-2 mb-2">
                  <Cloud className="w-5 h-5 text-purple-600" />
                  <span className="text-xs font-medium text-purple-700">{t('weather.pressure')}</span>
                </div>
                <p className="text-2xl font-bold text-purple-900">{weatherData.pressure_hpa?.toFixed(0) || 'N/A'} hPa</p>
              </div>

              {weatherData.gdd_accumulated != null && (
                <div className="bg-gradient-to-br from-emerald-50 to-emerald-100 rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Thermometer className="w-5 h-5 text-emerald-600" />
                    <span className="text-xs font-medium text-emerald-700">{t('weather.gdd') || 'GDD'}</span>
                  </div>
                  <p className="text-2xl font-bold text-emerald-900">{weatherData.gdd_accumulated.toFixed(0)}°D</p>
                  <p className="text-xs text-emerald-600 mt-1">{t('weather.gdd_base_10') || 'base 10°C'}</p>
                </div>
              )}

              {weatherData.eto_mm != null && (
                <div className="bg-gradient-to-br from-teal-50 to-teal-100 rounded-xl p-4">
                  <div className="flex items-center gap-2 mb-2">
                    <Droplets className="w-5 h-5 text-teal-600" />
                    <span className="text-xs font-medium text-teal-700">{t('weather.et0') || 'ET₀'}</span>
                  </div>
                  <p className="text-2xl font-bold text-teal-900">{weatherData.eto_mm.toFixed(1)} mm</p>
                  <p className="text-xs text-teal-600 mt-1">{t('weather.et0_desc') || 'Evapotranspiración'}</p>
                </div>
              )}
            </div>

            {/* Downscaling indicator */}
            {downscaling === 'applied' && (
              <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg flex items-center gap-2">
                <MapPin className="w-4 h-4 text-green-600" />
                <p className="text-xs text-green-800">
                  {t('weather.downscaling_active') || 'Datos corregidos para esta parcela (altitud/orientación/pendiente)'}
                </p>
              </div>
            )}

            {/* Forecast */}
            {forecast.length > 0 ? (
              <div>
                <h3 className="text-sm font-semibold text-gray-700 mb-3">{t('weather.forecast_5_days') || 'Previsión 5 días'}</h3>
                <div className="grid grid-cols-5 gap-2">
                  {forecast.slice(0, 5).map((day, idx) => (
                    <div key={idx} className="bg-gray-50 rounded-lg p-3 text-center">
                      <p className="text-xs text-gray-600 mb-2">
                        {new Date(day.fecha).toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric' })}
                      </p>
                      <div className="flex items-center justify-center gap-2 mb-2">
                        <span className="text-lg font-bold text-gray-900">{day.t_maxima?.toFixed(0) || 'N/A'}°</span>
                        <span className="text-sm text-gray-500">{day.t_minima?.toFixed(0) || 'N/A'}°</span>
                      </div>
                      <p className="text-xs text-gray-600">{day.estado_cielo || 'Despejado'}</p>
                      {day.precipitacion_proba && day.precipitacion_proba > 0 && (
                        <p className="text-xs text-blue-600 mt-1">💧 {day.precipitacion_proba.toFixed(0)}%</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                <p className="text-xs text-yellow-800">
                  ⚠️ Previsión no disponible. Los datos se están cargando en segundo plano.
                </p>
              </div>
            )}
          </>
        ) : (
          <div className="text-center py-12">
            <Cloud className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-gray-600 mb-4">{t('weather.no_data')}</p>
            <button
              onClick={() => setShowMunicipalitySearch(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition"
            >
              {t('weather.search_municipality_button')}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

