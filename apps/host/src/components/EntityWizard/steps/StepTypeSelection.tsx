import { useMemo, useState } from 'react';
import { Search, X, Activity, MapPin, ArrowRight } from 'lucide-react';
import { useWizard } from '../WizardContext';
import { ENTITY_TYPE_METADATA, MACRO_CATEGORIES, ENTITY_CATEGORIES } from '../entityTypes';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { SDMGuideInfo } from '../SDMGuideInfo';
import { Button, Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';

/* eslint-disable @typescript-eslint/no-explicit-any */
// Selection accent per entity colour; the background comes from a theme token.
const COLOR_MAP: Record<string, { border: string }> = {
  green:  { border: '#22c55e' },
  teal:   { border: '#14b8a6' },
  indigo: { border: '#6366f1' },
  blue:   { border: '#3b82f6' },
  purple: { border: '#a855f7' },
  orange: { border: '#f97316' },
  yellow: { border: '#eab308' },
  brown:  { border: '#92400e' },
  gray:   { border: '#6b7280' },
};

function getColorStyle(color: string, selected: boolean) {
  const c = COLOR_MAP[color] ?? COLOR_MAP.gray;
  return selected ? { borderColor: c.border } : {};
}

export function StepTypeSelection() {
  const { entityType, setEntityType, goNext } = useWizard();
  const { t } = useI18n();
  const [searchTerm, setSearchTerm] = useState('');
  const [activeMacro, setActiveMacro] = useState<keyof typeof MACRO_CATEGORIES | null>(null);
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  const handleQuickCreate = (type: string) => {
    setEntityType(type);
    goNext();
  };

  const filteredTypes = useMemo(() => {
    return Object.keys(ENTITY_TYPE_METADATA).filter(type => {
      const meta = ENTITY_TYPE_METADATA[type];
      if (activeMacro && meta.macroCategory !== activeMacro) return false;
      if (!searchTerm.trim()) return true;
      const s = searchTerm.toLowerCase();
      return (
        type.toLowerCase().includes(s) ||
        meta.keywords.some(k => k.toLowerCase().includes(s)) ||
        meta.description.toLowerCase().includes(s) ||
        t(`wizard.types.${type}`).toLowerCase().includes(s)
      );
    });
  }, [searchTerm, activeMacro, t]);

  const groupedTypes = useMemo(() => {
    const groups: Record<string, string[]> = {};
    for (const [cat, types] of Object.entries(ENTITY_CATEGORIES)) {
      const filtered = types.filter(ty => filteredTypes.includes(ty));
      if (filtered.length > 0) groups[cat] = filtered;
    }
    return groups;
  }, [filteredTypes]);

  const toggleCategory = (cat: string) =>
    setExpandedCategories(prev => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });

  return (
    <div className="space-y-5">
      {/* Quick actions — most common entities */}
      <div>
        <p className="text-xs font-medium text-nkz-muted uppercase tracking-wide mb-2">{t('wizard.type_selection.quick_access')}</p>
        <Button variant="primary"
          onClick={() => handleQuickCreate('AgriParcel')}
          className="w-full flex items-center justify-between px-5 py-4 rounded-xl transition-colors shadow-sm"
        >
          <div className="flex items-center gap-3">
            <MapPin className="w-5 h-5 flex-shrink-0" />
            <div className="text-left">
              <div className="font-semibold">{t('wizard.type_selection.new_parcel')}</div>
              <div className="text-xs text-white/70">{t('wizard.type_selection.new_parcel_hint')}</div>
            </div>
          </div>
          <ArrowRight className="w-5 h-5 flex-shrink-0" />
        </Button>
      </div>

      <SDMGuideInfo />

      <div className="border-t border-nkz-border pt-4">
        <h3 className="text-lg font-semibold mb-1">{t('wizard.type_selection.other_types')}</h3>
        <p className="text-sm text-nkz-text-secondary">{t('wizard.type_selection.other_types_hint')}</p>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 text-nkz-muted w-5 h-5" />
        <Input
          type="text"
          value={searchTerm}
          onChange={(e: any) => setSearchTerm(e.target.value)}
          placeholder={t('wizard.type_selection.search_placeholder')}
          className="w-full pl-12 pr-4 py-3 border-2 border-nkz-border rounded-xl focus:ring-2 focus:ring-nkz-accent-base focus:border-nkz-accent-base text-base"
        />
        {searchTerm && (
          <Button variant="ghost" onClick={() => setSearchTerm('')} className="absolute right-4 top-1/2 -translate-y-1/2 text-nkz-muted hover:text-nkz-text-secondary">
            <X className="w-5 h-5" />
          </Button>
        )}
      </div>

      {/* Macro category filters */}
      <div className="grid grid-cols-3 gap-3">
        {(Object.entries(MACRO_CATEGORIES) as [keyof typeof MACRO_CATEGORIES, typeof MACRO_CATEGORIES[keyof typeof MACRO_CATEGORIES]][]).map(([key, macro]) => {
          const Icon = macro.icon;
          const isActive = activeMacro === key;
          return (
            <Button variant="ghost"
              key={key}
              onClick={() => setActiveMacro(isActive ? null : key)}
              style={isActive ? getColorStyle(macro.color, true) : {}}
              className={`p-4 rounded-xl border-2 text-left transition-all ${isActive ? 'bg-nkz-surface-sunken' : 'border-nkz-border hover:border-nkz-border hover:bg-nkz-bg-secondary'}`}
            >
              <Icon className="w-6 h-6 mb-2 text-nkz-muted" style={isActive ? { color: COLOR_MAP[macro.color]?.border } : {}} />
              <div className="font-semibold text-sm">{t(`wizard.macro.${key}.label`)}</div>
              <div className="text-xs text-nkz-muted mt-0.5">{t(`wizard.macro.${key}.description`)}</div>
            </Button>
          );
        })}
      </div>

      {/* Results */}
      {searchTerm.trim() ? (
        <div className="space-y-2">
          <p className="text-sm text-nkz-text-secondary">{t(filteredTypes.length === 1 ? 'wizard.type_selection.results_one' : 'wizard.type_selection.results_other', { count: filteredTypes.length, term: searchTerm })}</p>
          {filteredTypes.length === 0 ? (
            <div className="p-6 text-center bg-nkz-bg-secondary rounded-xl">
              <Search className="w-10 h-10 text-nkz-text-muted mx-auto mb-2" />
              <p className="text-nkz-muted">{t('wizard.type_selection.no_results')}</p>
            </div>
          ) : (
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {filteredTypes.map(type => <TypeCard key={type} type={type} selected={entityType === type} onSelect={setEntityType} />)}
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
          {Object.entries(groupedTypes).map(([cat, types]) => (
            <div key={cat} className="border border-nkz-border rounded-lg overflow-hidden">
              <Button variant="ghost"
                onClick={() => toggleCategory(cat)}
                className="w-full px-4 py-3 flex items-center justify-between bg-nkz-bg-secondary hover:bg-nkz-bg-secondary transition"
              >
                <span className="font-medium text-sm text-nkz-text-primary">{t(`wizard.categories.${cat}`)}</span>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-nkz-muted bg-nkz-surface-sunken px-2 py-0.5 rounded-full">{types.length}</span>
                  {expandedCategories.has(cat)
                    ? <ChevronDown className="w-4 h-4 text-nkz-muted" />
                    : <ChevronRight className="w-4 h-4 text-nkz-muted" />}
                </div>
              </Button>
              {expandedCategories.has(cat) && (
                <div className="p-2 grid grid-cols-2 gap-2 bg-nkz-surface">
                  {types.map(type => <TypeCard key={type} type={type} selected={entityType === type} onSelect={setEntityType} compact />)}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Selection summary */}
      {entityType && (() => {
        const meta = ENTITY_TYPE_METADATA[entityType];
        const Icon = meta?.icon ?? Activity;
        return (
          <div className="p-4 bg-nkz-accent-soft border-2 border-nkz-accent-base rounded-xl flex items-center gap-3">
            <Icon className="w-6 h-6 text-nkz-accent-strong" />
            <div>
              <div className="font-semibold text-nkz-accent-strong">{entityType}</div>
              <div className="text-sm text-nkz-accent-strong">{t(`wizard.types.${entityType}`)}</div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ─── TypeCard sub-component ───────────────────────────────────────────────────

interface TypeCardProps {
  type: string;
  selected: boolean;
  onSelect: (type: string) => void;
  compact?: boolean;
}

function TypeCard({ type, selected, onSelect, compact = false }: TypeCardProps) {
  const { t } = useI18n();
  const meta = ENTITY_TYPE_METADATA[type];
  const Icon = meta?.icon ?? Activity;
  const style = selected && meta ? getColorStyle(meta.color, true) : {};
  const baseClass = `rounded-lg border-2 text-left transition flex items-${compact ? 'center' : 'start'} gap-${compact ? '2' : '3'} p-${compact ? '2.5' : '3'}`;
  const borderClass = selected ? 'bg-nkz-surface-sunken' : 'border-nkz-border hover:border-nkz-border hover:bg-nkz-bg-secondary';

  return (
    <Button variant="ghost"
      key={type}
      onClick={() => onSelect(type)}
      style={style}
      className={`${baseClass} ${borderClass}`}
    >
      <Icon className={`w-${compact ? '4' : '5'} h-${compact ? '4' : '5'} flex-shrink-0 text-nkz-muted`} />
      <div className="min-w-0">
        <div className={`font-medium text-${compact ? 'xs' : 'sm'} truncate`}>{type}</div>
        {!compact && <div className="text-xs text-nkz-muted truncate">{t(`wizard.types.${type}`)}</div>}
      </div>
    </Button>
  );
}
