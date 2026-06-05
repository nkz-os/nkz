import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Shield, Sliders, Webhook, RefreshCw,
  ChevronDown, ChevronRight, Zap, Clock, Filter,
} from 'lucide-react';
import api from '@/services/api';
import { SmartRiskPanel } from '@/components/SmartRiskPanel';
import { RiskWebhooksPanel } from '@/components/RiskWebhooksPanel';
import type { RiskState, RiskCatalog } from '@/types';
import { Button } from '@nekazari/ui-kit';

// ─── Constants ────────────────────────────────────────────────────────────────

const SEVERITY_CONFIG = {
  critical: { label: 'Crítico',  bg: 'bg-nkz-error-light',    text: 'text-red-800',    bar: 'bg-nkz-error-light0',    dot: 'bg-nkz-error-light0'    },
  high:     { label: 'Alto',     bg: 'bg-orange-100', text: 'text-orange-800', bar: 'bg-orange-500', dot: 'bg-orange-500' },
  medium:   { label: 'Medio',    bg: 'bg-nkz-warning-light', text: 'text-yellow-800', bar: 'bg-nkz-warning-light0', dot: 'bg-nkz-warning-light0' },
  low:      { label: 'Bajo',     bg: 'bg-nkz-bg-secondary',   text: 'text-gray-700',   bar: 'bg-gray-400',   dot: 'bg-gray-400'   },
  null:     { label: 'Sin datos',bg: 'bg-nkz-info-light',    text: 'text-nkz-info',   bar: 'bg-blue-300',   dot: 'bg-blue-300'   },
} as const;

const DOMAIN_EMOJI: Record<string, string> = {
  agronomic: '🌾', robotic: '🤖', energy: '⚡', livestock: '🐄', other: '⚠️',
};

const TABS = [
  { id: 'monitor',    label: 'Monitor',      icon: Shield   },
  { id: 'configure',  label: 'Configuración', icon: Sliders  },
  { id: 'webhooks',   label: 'Webhooks',      icon: Webhook  },
] as const;

type TabId = typeof TABS[number]['id'];

// ─── Helpers ──────────────────────────────────────────────────────────────────

function shortEntityId(id: string): string {
  // urn:ngsi-ld:AgriParcel:tenant:1234567 → AgriParcel · ...7
  const parts = id.split(':');
  if (parts.length >= 4) {
    const type = parts[2];
    const tail = parts[parts.length - 1].slice(-6);
    return `${type} · …${tail}`;
  }
  return id.slice(-20);
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleString('es', { dateStyle: 'short', timeStyle: 'short' });
}

// ─── Sub-components ───────────────────────────────────────────────────────────

interface SeverityBadgeProps { severity: RiskState['severity'] }
function SeverityBadge({ severity }: SeverityBadgeProps) {
  const cfg = SEVERITY_CONFIG[severity ?? 'null'];
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.bg} ${cfg.text}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
      {cfg.label}
    </span>
  );
}

interface ProbabilityBarProps { score: number; severity: RiskState['severity'] }
function ProbabilityBar({ score, severity }: ProbabilityBarProps) {
  const cfg = SEVERITY_CONFIG[severity ?? 'null'];
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div className="flex-1 bg-nkz-bg-secondary rounded-full h-1.5 min-w-[60px]">
        <div
          className={`h-1.5 rounded-full transition-all ${cfg.bar}`}
          style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
        />
      </div>
      <span className="text-xs font-mono text-gray-600 w-8 shrink-0">{score.toFixed(0)}%</span>
    </div>
  );
}

interface RiskRowProps { state: RiskState; catalog: Map<string, RiskCatalog> }
function RiskRow({ state, catalog }: RiskRowProps) {
  const [expanded, setExpanded] = useState(false);
  const risk = catalog.get(state.risk_code);
  const hasDetails = state.evaluation_data && Object.keys(state.evaluation_data).length > 0;

  return (
    <>
      <tr className="hover:bg-nkz-bg-secondary transition-colors border-b border-gray-100 last:border-0">
        <td className="px-4 py-3 text-sm">
          <div className="flex items-center gap-1.5">
            <span>{risk ? DOMAIN_EMOJI[risk.risk_domain] : '⚠️'}</span>
            <span className="font-medium text-gray-800">{risk?.risk_name ?? state.risk_code}</span>
          </div>
          <div className="text-xs text-nkz-muted mt-0.5">{state.risk_code}</div>
        </td>
        <td className="px-4 py-3"><SeverityBadge severity={state.severity} /></td>
        <td className="px-4 py-3 w-36"><ProbabilityBar score={state.probability_score} severity={state.severity} /></td>
        <td className="px-4 py-3 text-xs text-nkz-muted whitespace-nowrap">{formatTimestamp(state.timestamp)}</td>
        <td className="px-4 py-3 text-right">
          {hasDetails && (
            <Button onClick={() => setExpanded(v => !v)} className="text-nkz-muted hover:text-gray-700 transition p-1">
              {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
            </Button>
          )}
        </td>
      </tr>
      {expanded && hasDetails && (
        <tr className="bg-nkz-bg-secondary/50">
          <td colSpan={5} className="px-4 pb-3 pt-0">
            <div className="rounded-lg p-3 text-xs font-mono text-gray-600 grid grid-cols-2 md:grid-cols-3 gap-2 border border-gray-100">
              {Object.entries(state.evaluation_data).map(([k, v]) => (
                <div key={k} className="flex gap-1">
                  <span className="text-nkz-muted">{k}:</span>
                  <span className="text-gray-700 font-medium">{typeof v === 'number' ? v.toFixed(2) : String(v)}</span>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

interface ParcelGroupProps { 
  entityId: string; 
  states: RiskState[]; 
  catalog: Map<string, RiskCatalog> 
}
function ParcelGroup({ entityId, states, catalog }: ParcelGroupProps) {
  const [isExpanded, setIsExpanded] = useState(true);
  
  // Get max severity for this parcel using strict type safe logic
  const SEVERITY_ORDER: Record<string, number> = { 
    'critical': 4, 
    'high': 3, 
    'medium': 2, 
    'low': 1,
    'null': 0 
  };

  const maxSeverity = states.reduce((max, s) => {
    const currentSev = s.severity || 'null';
    const maxSev = max || 'null';
    
    return (SEVERITY_ORDER[currentSev] > SEVERITY_ORDER[maxSev]) 
      ? s.severity 
      : max;
  }, null as RiskState['severity']);

  return (
    <div className="bg-white rounded-xl border border-nkz-border overflow-hidden shadow-sm mb-4">
      {/* Header / Accordion trigger */}
      <Button 
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-nkz-bg-secondary transition-colors text-left border-b border-gray-100"
      >
        <div className="flex items-center gap-4">
          <div className="flex flex-col">
            <div className="flex items-center gap-2">
              <h3 className="font-bold text-gray-900">{shortEntityId(entityId)}</h3>
              <span className="text-xs text-nkz-muted font-mono hidden md:inline">{entityId}</span>
            </div>
            <div className="text-xs text-nkz-muted mt-0.5">
              {states.length} riesgos evaluados
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="flex flex-col items-end gap-1">
            <span className="text-[10px] text-nkz-muted uppercase font-bold tracking-wider">Estado Máximo</span>
            <SeverityBadge severity={maxSeverity} />
          </div>
          {isExpanded ? <ChevronDown className="w-5 h-5 text-nkz-muted" /> : <ChevronRight className="w-5 h-5 text-nkz-muted" />}
        </div>
      </Button>

      {/* Risks table */}
      {isExpanded && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-nkz-bg-secondary/50 border-b border-gray-100">
              <tr>
                <th className="px-4 py-2 text-left text-xs font-semibold text-nkz-muted uppercase tracking-wider">Riesgo</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-nkz-muted uppercase tracking-wider">Severidad</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-nkz-muted uppercase tracking-wider">Probabilidad</th>
                <th className="px-4 py-2 text-left text-xs font-semibold text-nkz-muted uppercase tracking-wider">Fecha</th>
                <th className="px-4 py-2 w-10"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {states.map(s => (
                <RiskRow key={`${s.entity_id}-${s.risk_code}`} state={s} catalog={catalog} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Monitor tab ──────────────────────────────────────────────────────────────

function MonitorTab() {
  const [states, setStates] = useState<RiskState[]>([]);
  const [catalog, setCatalog] = useState<Map<string, RiskCatalog>>(new Map());
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);
  const [triggerMsg, setTriggerMsg] = useState<string | null>(null);
  const [filterSeverity, setFilterSeverity] = useState<string>('all');
  const [filterCode, setFilterCode] = useState<string>('all');
  const [lastRefresh, setLastRefresh] = useState<Date>(new Date());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadStates = useCallback(async () => {
    try {
      const [statesData, catalogData] = await Promise.all([
        api.getRiskStates({ limit: 100 }),
        api.getRiskCatalog(),
      ]);
      setStates(statesData);
      setCatalog(new Map(catalogData.map(r => [r.risk_code, r])));
      setLastRefresh(new Date());
    } catch {
      // keep previous state on error
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStates();
    intervalRef.current = setInterval(loadStates, 60_000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [loadStates]);

  const handleTrigger = async () => {
    setTriggering(true);
    setTriggerMsg(null);
    try {
      await api.triggerRiskEvaluation();
      setTriggerMsg('Evaluación en curso. Los resultados estarán disponibles en ~30 segundos.');
      setTimeout(() => loadStates(), 35_000);
    } catch (err: any) {
      const errMsg = err?.response?.data?.error || err.message || 'Error desconocido';
      setTriggerMsg(`Error al disparar la evaluación: ${errMsg}`);
    } finally {
      setTriggering(false);
    }
  };

  // Counts by severity for the stat cards
  const severityCounts = states.reduce((acc, s) => {
    const k = s.severity ?? 'null';
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  // Unique risk codes for filter
  const allCodes = [...new Set(states.map(s => s.risk_code))];

  // Filtered + Grouped
  const filtered = states
    .filter(s => {
      if (filterSeverity !== 'all' && (s.severity ?? 'null') !== filterSeverity) return false;
      if (filterCode !== 'all' && s.risk_code !== filterCode) return false;
      return true;
    });

  // Group by entity_id
  const groups = filtered.reduce((acc, s) => {
    if (!acc[s.entity_id]) acc[s.entity_id] = [];
    acc[s.entity_id].push(s);
    return acc;
  }, {} as Record<string, RiskState[]>);

  // Sorted entity IDs by max severity
  const SEVERITY_WEIGHT: Record<string, number> = { critical: 4, high: 3, medium: 2, low: 1, null: 0 };
  const sortedEntityIds = Object.keys(groups).sort((a, b) => {
    const maxA = Math.max(...groups[a].map(s => SEVERITY_WEIGHT[s.severity ?? 'null'] ?? 0));
    const maxB = Math.max(...groups[b].map(s => SEVERITY_WEIGHT[s.severity ?? 'null'] ?? 0));
    return maxB - maxA;
  });

  return (
    <div className="space-y-6">
      {/* Stat cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {(['critical', 'high', 'medium', 'low'] as const).map(sev => {
          const cfg = SEVERITY_CONFIG[sev];
          const count = severityCounts[sev] ?? 0;
          return (
            <Button
              key={sev}
              onClick={() => setFilterSeverity(filterSeverity === sev ? 'all' : sev)}
              className={`rounded-xl border-2 p-4 text-left transition-all ${
                filterSeverity === sev
                  ? `${cfg.bg} border-current`
                  : 'bg-white border-nkz-border hover:border-nkz-border'
              }`}
            >
              <div className={`text-3xl font-bold ${filterSeverity === sev ? cfg.text : 'text-gray-900'}`}>{count}</div>
              <div className={`text-sm font-medium mt-1 ${filterSeverity === sev ? cfg.text : 'text-nkz-muted'}`}>{cfg.label}</div>
            </Button>
          );
        })}
      </div>

      {/* Trigger evaluation + refresh */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Button
            onClick={handleTrigger}
            disabled={triggering}
            className="flex items-center gap-2 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium disabled:opacity-50 transition"
          >
            {triggering
              ? <><RefreshCw className="w-4 h-4 animate-spin" /> Evaluando...</>
              : <><Zap className="w-4 h-4" /> Disparar evaluación</>
            }
          </Button>
          <Button
            onClick={() => { setLoading(true); loadStates(); }}
            disabled={loading}
            className="p-2 text-nkz-muted hover:text-gray-700 hover:bg-nkz-bg-secondary rounded-lg transition"
            title="Actualizar"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>
        <div className="flex items-center gap-1 text-xs text-nkz-muted">
          <Clock className="w-3 h-3" />
          Actualizado: {formatTimestamp(lastRefresh.toISOString())} · Auto-refresh 60s
        </div>
      </div>

      {triggerMsg && (
        <div className={`p-3 border rounded-lg text-sm ${triggerMsg.includes('Error') ? 'bg-nkz-error-light border-red-200 text-red-800' : 'bg-nkz-info-light border-blue-200 text-blue-800'}`}>
          {triggerMsg}
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <Filter className="w-4 h-4 text-nkz-muted" />
        <select
          value={filterCode}
          onChange={(e: any) => setFilterCode(e.target.value)}
          className="text-sm border border-nkz-border rounded-lg px-3 py-1.5 bg-white focus:ring-2 focus:ring-green-500 outline-none"
        >
          <option value="all">Todos los riesgos</option>
          {allCodes.map(code => (
            <option key={code} value={code}>{catalog.get(code)?.risk_name ?? code}</option>
          ))}
        </select>
        {filterSeverity !== 'all' && (
          <Button
            onClick={() => setFilterSeverity('all')}
            className="text-xs text-nkz-muted hover:text-gray-700 underline"
          >
            Quitar filtro de severidad
          </Button>
        )}
        <span className="text-xs text-nkz-muted ml-auto">{filtered.length} riesgos detectados</span>
      </div>

      {/* Grouped View */}
      {loading ? (
        <div className="flex items-center justify-center py-16 text-nkz-muted">
          <RefreshCw className="w-6 h-6 animate-spin mr-2" /> Cargando...
        </div>
      ) : sortedEntityIds.length === 0 ? (
        <div className="text-center py-16 bg-nkz-bg-secondary rounded-xl border border-dashed border-nkz-border">
          <Shield className="w-12 h-12 text-gray-300 mx-auto mb-3" />
          <p className="text-nkz-muted font-medium">
            {states.length === 0
              ? 'No hay evaluaciones de riesgo. Pulsa "Disparar evaluación" para iniciar.'
              : 'No hay resultados con los filtros actuales.'}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {sortedEntityIds.map(entityId => (
            <ParcelGroup 
              key={entityId} 
              entityId={entityId} 
              states={groups[entityId]} 
              catalog={catalog} 
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export const Risks: React.FC = () => {
  const [activeTab, setActiveTab] = useState<TabId>('monitor');

  return (
    <div className="container mx-auto px-4 py-6 max-w-7xl">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-2">
          <Shield className="text-nkz-success h-8 w-8" />
          Inteligencia de Riesgos
        </h1>
        <p className="text-nkz-muted mt-1">
          Monitorización proactiva y modelización de amenazas agroclimáticas mediante SDM y NGSI-LD.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-nkz-border mb-6">
        {TABS.map(tab => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <Button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors -mb-px ${
                isActive
                  ? 'border-green-600 text-nkz-success'
                  : 'border-transparent text-nkz-muted hover:text-gray-700 hover:border-nkz-border'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </Button>
          );
        })}
      </div>

      {/* Tab content */}
      <div className="mt-6">
        {activeTab === 'monitor'   && <MonitorTab />}
        {activeTab === 'configure' && <SmartRiskPanel />}
        {activeTab === 'webhooks'  && <RiskWebhooksPanel />}
      </div>
    </div>
  );
};
