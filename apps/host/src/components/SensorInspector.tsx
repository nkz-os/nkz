// =============================================================================
// Sensor Inspector - Panel lateral sobre el mapa con telemetría Chart.js
// =============================================================================

import React, { useState, useEffect, useCallback } from 'react';
import {
    X,
    Activity,
    Thermometer,
    Droplets,
    Battery,
    Clock,
    RefreshCw,
    Gauge,
    MapPin,
    Cpu,
    Cable,
    Pencil,
    CloudRain,
} from 'lucide-react';
import {
    Chart as ChartJS,
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    Filler,
    Tooltip as ChartTooltip,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import api from '@/services/api';
import { openEntityEditor } from '@/components/EntityEditor';
import { ConnectivityPanel } from './connectivity';
import { ManagementPanel } from './management';
import { Settings } from 'lucide-react';
import { logger } from '@/utils/logger';
import { Button } from '@nekazari/ui-kit';


// Register Chart.js components
ChartJS.register(
    CategoryScale,
    LinearScale,
    PointElement,
    LineElement,
    Filler,
    ChartTooltip,
);


// =============================================================================
// Types
// =============================================================================

interface TelemetryDataPoint {
    timestamp: string;
    value: number;
}

interface SensorInspectorProps {
    entity: {
        id: string;
        type: string;
        name: string;
        data?: any;
    } | null;
    onClose: () => void;
    isOpen: boolean;
}

interface TelemetryState {
    temperature: TelemetryDataPoint[];
    humidity: TelemetryDataPoint[];
    battery: TelemetryDataPoint[];
    [key: string]: TelemetryDataPoint[];
}

// =============================================================================
// Mini Chart Component
// =============================================================================

interface MiniChartProps {
    data: TelemetryDataPoint[];
    color: string;
    unit: string;
    label: string;
    icon: React.ReactNode;
}

const MiniChart: React.FC<MiniChartProps> = ({ data, color, unit, label, icon }) => {
    const latestValue = data.length > 0 ? data[data.length - 1].value : null;

    const chartData = {
        labels: data.map(d => d.timestamp),
        datasets: [{
            data: data.map(d => d.value),
            borderColor: color,
            borderWidth: 2,
            backgroundColor: `${color}30`,
            fill: true,
            pointRadius: 0,
            tension: 0.4,
        }],
    };

    const chartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            tooltip: { enabled: false },
        },
        scales: {
            x: { display: false },
            y: { display: false },
        },
    } as const;

    return (
        <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/50">
            <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                    <div className={`p-1.5 rounded-lg`} style={{ backgroundColor: `${color}20` }}>
                        {icon}
                    </div>
                    <span className="text-sm text-nkz-muted">{label}</span>
                </div>
                <span className="text-lg font-bold text-white">
                    {latestValue !== null ? `${latestValue.toFixed(1)}${unit}` : '--'}
                </span>
            </div>
            <div className="h-16">
                <Line data={chartData} options={chartOptions} />
            </div>
        </div>
    );
};

// =============================================================================
// Main Component
// =============================================================================

/** Extract numeric value from an NGSI-LD entity attribute (Property, Relationship, or raw). */
function getEntityAttrValue(entityData: any, key: string): number | null {
    if (!entityData) return null;
    const attr = entityData[key];
    if (attr === null || attr === undefined) return null;
    if (typeof attr === 'number') return attr;
    if (typeof attr === 'object' && 'value' in attr) {
        const v = attr.value;
        if (typeof v === 'number') return v;
        if (typeof v === 'string') { const n = parseFloat(v); return isNaN(n) ? null : n; }
    }
    return null;
}

// ===========================================================================
// Canonical weather attribute descriptor — single source of truth.
// NGSI-LD names MUST match the timeseries-reader _WEATHER_ATTRIBUTE_MAP
// (/nkz/services/timeseries-reader/app.py). DB column names are the
// resolved targets used in the v2 columnar response.
// ===========================================================================

interface WeatherAttrDescriptor {
    /** NGSI-LD attribute name as stored in Orion-LD (canonical SDM short name). */
    ngsiLd: string;
    /** TimescaleDB column name returned in the v2 columnar response. */
    dbColumn: string;
    /** Human-readable label shown in the UI card. */
    label: string;
    /** Unit symbol (e.g. "°C", "%", "hPa", "m/s", "mm"). */
    unit: string;
    /** Tailwind color classes for the card (bg / text). */
    color: { bg: string; text: string };
}

const WEATHER_ATTRS: WeatherAttrDescriptor[] = [
    { ngsiLd: 'temperature',         dbColumn: 'temp_avg',        label: 'Temperatura',   unit: '°C',  color: { bg: 'from-red-900/30 to-orange-900/30',   text: 'text-red-300'   } },
    { ngsiLd: 'relativeHumidity',    dbColumn: 'humidity_avg',    label: 'Humedad',       unit: '%',   color: { bg: 'from-blue-900/30 to-cyan-900/30',    text: 'text-blue-300'  } },
    { ngsiLd: 'atmosphericPressure', dbColumn: 'pressure_hpa',    label: 'Presión',        unit: 'hPa', color: { bg: 'from-purple-900/30 to-indigo-900/30', text: 'text-purple-300' } },
    { ngsiLd: 'windSpeed',           dbColumn: 'wind_speed_ms',   label: 'Viento',         unit: 'm/s', color: { bg: 'from-teal-900/30 to-emerald-900/30',  text: 'text-teal-300'  } },
    { ngsiLd: 'precipitation',       dbColumn: 'precip_mm',       label: 'Precipitación',  unit: 'mm',  color: { bg: 'from-sky-900/30 to-blue-900/30',     text: 'text-sky-300'   } },
];

/** Set of entity types that represent weather/virtual stations. */
const WEATHER_ENTITY_TYPES = new Set(['WeatherObserved', 'WeatherStation']);

/** Build the canonical attrs=… query-string value for the v2 timeseries endpoint. */
const WEATHER_ATTRS_CSV = WEATHER_ATTRS.map((d) => d.ngsiLd).join(',');

export const SensorInspector: React.FC<SensorInspectorProps> = ({
    entity,
    onClose,
    isOpen
}) => {
    const [telemetry, setTelemetry] = useState<TelemetryState>({
        temperature: [],
        humidity: [],
        battery: []
    });
    const [weatherCharts, setWeatherCharts] = useState<Record<string, TelemetryDataPoint[]>>({});
    const [loading, setLoading] = useState(false);
    const [timeRange, setTimeRange] = useState<'1h' | '6h' | '24h'>('24h');
    const [lastUpdate, setLastUpdate] = useState<Date | null>(null);
    const [activeTab, setActiveTab] = useState<'telemetry' | 'connectivity' | 'management'>('telemetry');

    const isWeatherEntity = WEATHER_ENTITY_TYPES.has(entity?.type || '');

    // Load telemetry data
    const loadTelemetry = useCallback(async () => {
        if (!entity?.id) return;

        setLoading(true);
        try {
            const endTime = new Date().toISOString();
            const hours = timeRange === '1h' ? 1 : timeRange === '6h' ? 6 : 24;
            const startTime = new Date(Date.now() - hours * 3600000).toISOString();

            if (isWeatherEntity) {
                // WeatherObserved / WeatherStation: use timeseries-reader v2 which resolves
                // the URN to a weather key (municipality_code) via plan_timeseries_read.
                // v1 does NOT support URN resolution — it would query with the raw entity_id
                // which never matches station_id or municipality_code.
                //
                // Attribute names MUST match the canonical _WEATHER_ATTRIBUTE_MAP in
                // timeseries-reader/app.py. Using non-canonical names (e.g. "humidity"
                // instead of "relativeHumidity") causes a 400 response for the ENTIRE
                // batch because v2 validates every attribute before querying.
                const chartData: Record<string, TelemetryDataPoint[]> = {};

                try {
                    const v2url = `/api/timeseries/v2/entities/${encodeURIComponent(entity.id)}/data`;
                    const v2res = await api.get(v2url, {
                        params: {
                            time_from: startTime,
                            time_to: endTime,
                            attrs: WEATHER_ATTRS_CSV,
                            limit: 200,
                        },
                    });
                    const body = v2res.data;
                    // v2 columnar format: { timestamps: string[], attributes: { temp_avg: number[], ... } }
                    if (body?.timestamps && body?.attributes) {
                        for (const desc of WEATHER_ATTRS) {
                            const values = body.attributes[desc.dbColumn] as (number | null)[] | undefined;
                            if (!values || values.length === 0) continue;
                            const points: TelemetryDataPoint[] = [];
                            for (let i = 0; i < Math.min(body.timestamps.length, values.length); i++) {
                                const v = values[i];
                                if (v !== null && v !== undefined) {
                                    points.push({
                                        timestamp: new Date(body.timestamps[i]).toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit' }),
                                        value: Number(v),
                                    });
                                }
                            }
                            if (points.length > 0) chartData[desc.ngsiLd] = points;
                        }
                    }
                } catch { /* non-fatal — latest readings still shown from entity.data */ }

                setWeatherCharts(chartData);
                // Populate telemetry state for MiniChart compatibility:
                // temperature → MiniChart with temp data, humidity → humidity,
                // battery slot re-used for pressure+wind overlay
                setTelemetry({
                    temperature: chartData.temperature || [],
                    humidity: chartData.relativeHumidity || [],
                    battery: [
                        ...(chartData.atmosphericPressure || []),
                        ...(chartData.windSpeed || []),
                    ],
                });
                setLastUpdate(new Date());
            } else {
                // IoT sensors: use device telemetry API
                const response = await api.getDeviceTelemetry(entity.id, {
                    start_time: startTime,
                    end_time: endTime,
                    limit: 100
                });

                if (response?.events) {
                    const tempData: TelemetryDataPoint[] = [];
                    const humData: TelemetryDataPoint[] = [];
                    const batData: TelemetryDataPoint[] = [];

                    response.events.forEach((event: any) => {
                        const ts = new Date(event.observed_at).toLocaleTimeString('es', {
                            hour: '2-digit',
                            minute: '2-digit'
                        });

                        if (event.payload?.temperature !== undefined) {
                            tempData.push({ timestamp: ts, value: event.payload.temperature });
                        }
                        if (event.payload?.humidity !== undefined) {
                            humData.push({ timestamp: ts, value: event.payload.humidity });
                        }
                        if (event.payload?.battery !== undefined || event.payload?.batteryLevel !== undefined) {
                            batData.push({
                                timestamp: ts,
                                value: event.payload.battery || event.payload.batteryLevel
                            });
                        }
                    });

                    setTelemetry({
                        temperature: tempData,
                        humidity: humData,
                        battery: batData
                    });
                    setLastUpdate(new Date());
                }
            }
        } catch (error) {
            logger.error('Error loading telemetry:', error);
        } finally {
            setLoading(false);
        }
    }, [entity?.id, entity?.type, timeRange, isWeatherEntity]);

    // Load on entity change or time range change
    useEffect(() => {
        if (isOpen && entity) {
            loadTelemetry();
        }
    }, [isOpen, entity, timeRange, loadTelemetry]);

    // Auto-refresh every 30 seconds
    useEffect(() => {
        if (!isOpen || !entity) return;

        const interval = setInterval(loadTelemetry, 30000);
        return () => clearInterval(interval);
    }, [isOpen, entity, loadTelemetry]);

    if (!isOpen || !entity) return null;

    const entityType = entity.type || 'Device';
    const entityName = entity.name || entity.id;

    // Latest reading from entity.data (fallback for weather entities when timeseries is empty)
    // Latest readings from the NGSI-LD entity (fallback when timeseries is empty).
    // Uses canonical NGSI-LD names from WEATHER_ATTRS descriptor — same names
    // stored in Orion-LD when @context is properly applied.
    const entityData = entity?.data;
    const weatherLatest: Record<string, number | null> = {};
    for (const desc of WEATHER_ATTRS) {
        weatherLatest[desc.ngsiLd] = getEntityAttrValue(entityData, desc.ngsiLd);
    }
    const hasAnyWeatherLatest = Object.values(weatherLatest).some((v) => v !== null);
    const hasAnyWeatherChart = Object.keys(weatherCharts).length > 0;

    // Build Chart.js config for a given dataset
    const buildChartData = (data: TelemetryDataPoint[], color: string) => ({
        labels: data.map(d => d.timestamp),
        datasets: [{
            data: data.map(d => d.value),
            borderColor: color,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBackgroundColor: color,
            tension: 0.4,
        }],
    });

    const mainChartData = buildChartData(telemetry.temperature, '#f97316');

    const mainChartOptions = {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            tooltip: {
                backgroundColor: 'rgba(17, 24, 39, 0.9)',
                titleColor: '#9ca3af',
                bodyColor: '#fff',
                titleFont: { size: 11 },
                bodyFont: { size: 13, weight: 'bold' as const },
                padding: 8,
                cornerRadius: 8,
                borderColor: '#374151',
                borderWidth: 1,
            },
        },
        scales: {
            x: {
                ticks: { color: '#6b7280', font: { size: 10 } },
                grid: { color: '#374151', drawBorder: false },
            },
            y: {
                ticks: { color: '#6b7280', font: { size: 10 } },
                grid: { color: '#374151', drawBorder: false },
            },
        },
    } as const;

    return (
        <div className="absolute top-0 right-0 h-full w-96 bg-gray-900 shadow-2xl z-50 flex flex-col border-l border-gray-700/50">
            {/* Header */}
            <div className="p-4 border-b border-gray-700/50 flex-shrink-0">
                <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-nkz-info/20 rounded-lg">
                            <Cpu className="w-5 h-5 text-blue-400" />
                        </div>
                        <div>
                            <h3 className="text-white font-semibold truncate max-w-[200px]">
                                {entityName}
                            </h3>
                            <p className="text-nkz-muted text-xs">{entityType}</p>
                        </div>
                    </div>
                    <div className="flex items-center gap-1">
                        <Button
                            onClick={() => openEntityEditor(entity.id, entity.type)}
                            className="px-3 py-1.5 text-xs bg-nkz-info-soft text-nkz-info rounded-lg hover:bg-nkz-info-soft transition flex items-center gap-1"
                            title="Editar entidad"
                        >
                            <Pencil className="w-3.5 h-3.5" />
                            Editar
                        </Button>
                        <Button
                            onClick={onClose}
                            className="p-2 text-nkz-muted hover:text-white hover:bg-gray-700/50 rounded-lg transition-colors"
                        >
                            <X className="w-5 h-5" />
                        </Button>
                    </div>
                </div>

                {/* Time Range Selector */}
                <div className="flex gap-2 mt-4">
                    {(['1h', '6h', '24h'] as const).map((range) => (
                        <Button
                            key={range}
                            onClick={() => setTimeRange(range)}
                            className={`px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${timeRange === range
                                ? 'bg-nkz-info text-white'
                                : 'bg-gray-800 text-nkz-muted hover:bg-gray-700'
                                }`}
                        >
                            {range}
                        </Button>
                    ))}
                    <Button
                        onClick={loadTelemetry}
                        disabled={loading}
                        className="ml-auto p-1.5 text-nkz-muted hover:text-white hover:bg-gray-700/50 rounded-lg transition-colors"
                    >
                        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
                    </Button>
                </div>

                {/* Tab Navigation */}
                <div className="flex gap-1 mt-4 border-b border-gray-700/50 pb-0">
                    <Button
                        onClick={() => setActiveTab('telemetry')}
                        className={`flex items-center gap-2 px-3 py-2 text-xs font-medium rounded-t-lg transition-colors ${activeTab === 'telemetry'
                            ? 'bg-gray-800 text-white border-b-2 border-blue-500'
                            : 'text-nkz-muted hover:text-white hover:bg-gray-800/50'
                            }`}
                    >
                        <Activity className="w-3.5 h-3.5" />
                        Telemetría
                    </Button>
                    <Button
                        onClick={() => setActiveTab('connectivity')}
                        className={`flex items-center gap-2 px-3 py-2 text-xs font-medium rounded-t-lg transition-colors ${activeTab === 'connectivity'
                            ? 'bg-gray-800 text-white border-b-2 border-purple-500'
                            : 'text-nkz-muted hover:text-white hover:bg-gray-800/50'
                            }`}
                    >
                        <Cable className="w-3.5 h-3.5" />
                        Conectividad
                    </Button>
                    <Button
                        onClick={() => setActiveTab('management')}
                        className={`flex items-center gap-2 px-3 py-2 text-xs font-medium rounded-t-lg transition-colors ${activeTab === 'management'
                            ? 'bg-gray-800 text-white border-b-2 border-red-500'
                            : 'text-nkz-muted hover:text-white hover:bg-gray-800/50'
                            }`}
                    >
                        <Settings className="w-3.5 h-3.5" />
                        Gestión
                    </Button>


                </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto p-4 space-y-4">
                {activeTab === 'telemetry' ? (
                    /* Telemetry Tab */
                    <>
                        {/* --- Weather entity: show latest readings + charts from timeseries --- */}
                        {isWeatherEntity && (hasAnyWeatherLatest || hasAnyWeatherChart) ? (
                            <>
                                {/* Latest reading cards driven by canonical WEATHER_ATTRS descriptor */}
                                <div className="grid grid-cols-2 gap-3">
                                    {WEATHER_ATTRS.map((desc) => {
                                        const val = weatherLatest[desc.ngsiLd];
                                        if (val === null) return null;
                                        return (
                                            <div key={desc.ngsiLd}
                                                className={`bg-gradient-to-br ${desc.color.bg} p-3 rounded-lg border border-white/10`}
                                            >
                                                <div className="flex items-center gap-2 mb-1">
                                                    <span className={`text-xs font-semibold ${desc.color.text}`}>{desc.label}</span>
                                                </div>
                                                <div className={`text-xl font-bold ${desc.color.text}`}>
                                                    {val.toFixed(1)}{desc.unit}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>

                                {/* MiniCharts driven by canonical WEATHER_ATTRS descriptor */}
                                {WEATHER_ATTRS.map((desc) => {
                                    const data = weatherCharts[desc.ngsiLd];
                                    if (!data || data.length === 0) return null;
                                    const chartColors: Record<string, string> = {
                                        temperature: '#f97316',
                                        relativeHumidity: '#3b82f6',
                                        atmosphericPressure: '#a855f7',
                                        windSpeed: '#14b8a6',
                                        precipitation: '#0ea5e9',
                                    };
                                    return (
                                        <MiniChart
                                            key={desc.ngsiLd}
                                            data={data}
                                            color={chartColors[desc.ngsiLd] || '#6b7280'}
                                            unit={desc.unit}
                                            label={desc.label}
                                            icon={<Activity className="w-4 h-4" />}
                                        />
                                    );
                                })}

                                {/* Temperature trend chart */}
                                {weatherCharts.temperature && weatherCharts.temperature.length > 0 && (
                                    <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/50">
                                        <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                                            <Activity className="w-4 h-4" />
                                            Tendencia de Temperatura
                                        </h4>
                                        <div className="h-48">
                                            <Line data={buildChartData(weatherCharts.temperature, '#f97316')} options={mainChartOptions} />
                                        </div>
                                    </div>
                                )}
                            </>
                        ) : isWeatherEntity ? (
                            /* Weather entity but no data at all */
                            <div className="text-center py-12 text-nkz-muted">
                                <CloudRain className="w-12 h-12 mx-auto mb-3 opacity-50" />
                                <p>Sin datos meteorológicos</p>
                                <p className="text-xs mt-1">La estación no tiene observaciones recientes.</p>
                            </div>
                        ) : (
                            /* --- IoT Sensor (original behavior) --- */
                            <>
                                {telemetry.temperature.length > 0 && (
                                    <MiniChart
                                        data={telemetry.temperature}
                                        color="#f97316"
                                        unit="°C"
                                        label="Temperatura"
                                        icon={<Thermometer className="w-4 h-4 text-orange-400" />}
                                    />
                                )}

                                {telemetry.humidity.length > 0 && (
                                    <MiniChart
                                        data={telemetry.humidity}
                                        color="#3b82f6"
                                        unit="%"
                                        label="Humedad"
                                        icon={<Droplets className="w-4 h-4 text-blue-400" />}
                                    />
                                )}

                                {telemetry.battery.length > 0 && (
                                    <MiniChart
                                        data={telemetry.battery}
                                        color="#22c55e"
                                        unit="%"
                                        label="Batería"
                                        icon={<Battery className="w-4 h-4 text-green-400" />}
                                    />
                                )}

                                {/* Main Chart (if has data) */}
                                {telemetry.temperature.length > 0 && (
                                    <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/50">
                                        <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                                            <Activity className="w-4 h-4" />
                                            Tendencia de Temperatura
                                        </h4>
                                        <div className="h-48">
                                            <Line data={mainChartData} options={mainChartOptions} />
                                        </div>
                                    </div>
                                )}

                                {/* No data message */}
                                {Object.values(telemetry).every(arr => arr.length === 0) && !loading && (
                                    <div className="text-center py-12 text-nkz-muted">
                                        <Gauge className="w-12 h-12 mx-auto mb-3 opacity-50" />
                                        <p>Sin datos de telemetría</p>
                                        <p className="text-xs mt-1">Esperando datos del dispositivo...</p>
                                    </div>
                                )}
                            </>
                        )}

                        {/* Entity Details */}
                        <div className="bg-gray-800/50 rounded-xl p-4 border border-gray-700/50">
                            <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                                <MapPin className="w-4 h-4" />
                                Detalles
                            </h4>
                            <div className="space-y-2 text-sm">
                                <div className="flex justify-between">
                                    <span className="text-nkz-muted">ID</span>
                                    <span className="text-gray-300 font-mono text-xs truncate max-w-[180px]" title={entity.id}>
                                        {entity.id}
                                    </span>
                                </div>
                                <div className="flex justify-between">
                                    <span className="text-nkz-muted">Tipo</span>
                                    <span className="text-gray-300">{entityType}</span>
                                </div>
                                {lastUpdate && (
                                    <div className="flex justify-between">
                                        <span className="text-nkz-muted">Actualizado</span>
                                        <span className="text-gray-300 flex items-center gap-1">
                                            <Clock className="w-3 h-3" />
                                            {lastUpdate.toLocaleTimeString('es')}
                                        </span>
                                    </div>
                                )}
                            </div>
                        </div>
                    </>
                ) : (
                    /* Other Tabs */
                    activeTab === 'connectivity' ? (
                        <ConnectivityPanel
                            entityId={entity.id}
                            entityType={entityType}
                            entityName={entityName}
                        />
                    ) : (
                        <ManagementPanel
                            entity={entity}
                            onUpdate={() => {
                                loadTelemetry();
                            }}
                            onDelete={() => {
                                onClose();
                                window.location.reload();
                            }}
                        />
                    )
                )}

            </div>
        </div>
    );
};

export default SensorInspector;
