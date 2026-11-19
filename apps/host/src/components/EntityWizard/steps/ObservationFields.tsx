import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Input, Select } from '@nekazari/ui-kit';
import api from '@/services/api';
import { relationshipTargets } from '@/utils/entityRelations';
import {
  FEATURE_TYPES,
  MEDIA,
  MEDIUM_FIELDS,
  ORGANS,
  contextErrors,
  contextForMedium,
  isMedium,
  type ContextField,
  type ObservationContext,
} from '@/utils/observationContext';

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface ChannelOverride {
  observesFeature?: string;
  observationContext?: ObservationContext;
}

export interface ObservationValue {
  observesFeature?: string;
  observationContext?: ObservationContext;
  channelOverrides?: Record<string, ChannelOverride>;
  mobility?: 'fixed' | 'mobile';
  /** ISO-8601 with offset. */
  validFrom?: string;
}

interface ObservationFieldsProps {
  value: ObservationValue;
  onChange: (value: ObservationValue) => void;
  /** `sdmAttribute` of every measurement of the selected profile. */
  profileAttributes: string[];
  /** URNs of the parcels the sensor measures; features are limited to them. */
  parcelIds: string[];
}

interface FeatureOption {
  id: string;
  type: string;
  name: string;
}

/** Select does not accept an empty value. */
const NONE = '__none__';
const PARENT_RELATIONS = ['hasAgriParcel', 'refAgriParcel', 'locatedAt', 'belongsTo'];

function entityName(entity: any): string {
  const raw = entity?.name?.value ?? entity?.name;
  return typeof raw === 'string' && raw ? raw : String(entity?.id ?? '');
}

/** Whether the entity is one of the selected parcels or stands under one of them. */
function underParcels(entity: any, parcelIds: string[]): boolean {
  if (parcelIds.includes(String(entity?.id))) return true;
  return PARENT_RELATIONS.some((rel) =>
    relationshipTargets(entity?.[rel]).some((urn) => parcelIds.includes(urn)),
  );
}

/** Parcels (and zones), plants, greenhouses and solar parks of the selected parcels. */
function useFeatureOptions(parcelIds: string[]): FeatureOption[] {
  const [options, setOptions] = useState<FeatureOption[]>([]);
  const key = parcelIds.join('|');
  useEffect(() => {
    let active = true;
    if (parcelIds.length === 0) {
      setOptions([]);
      return undefined;
    }
    Promise.all(
      FEATURE_TYPES.map((type) =>
        Promise.resolve(api.getSDMEntityInstances(type))
          .then((list: any[]) => (list ?? []).filter((e) => underParcels(e, parcelIds)).map((e) => ({ id: String(e.id), type, name: entityName(e) })))
          .catch(() => [] as FeatureOption[]),
      ),
    ).then((lists) => active && setOptions(lists.flat()));
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return options;
}

const NUMBER_FIELDS: ContextField[] = ['heightAboveGround', 'depthFrom', 'depthTo'];

interface ContextEditorProps {
  testPrefix: string;
  context: ObservationContext;
  features: FeatureOption[];
  onChange: (context: ObservationContext) => void;
}

/** The inputs of one medium. Shows the validation errors of the context. */
function ContextEditor({ testPrefix, context, features, onChange }: ContextEditorProps) {
  const { t } = useTranslation();
  const { required, optional } = MEDIUM_FIELDS[context.medium];
  const fields = [...required, ...optional];
  const errors = contextErrors(context);

  const setField = (field: ContextField, raw: string) => {
    const next: any = { ...context };
    if (raw === '' || raw === NONE) delete next[field];
    else next[field] = NUMBER_FIELDS.includes(field) ? Number(raw) : raw;
    onChange(next);
  };

  return (
    <div className="space-y-3">
      {fields.map((field) => {
        const label = t(`wizard.observation.field.${field}`) + (required.includes(field) ? ' *' : '');
        const current = context[field];
        return (
          <div key={field}>
            <label className="block text-xs font-medium text-nkz-text-secondary mb-0.5">{label}</label>
            {NUMBER_FIELDS.includes(field) && (
              <Input
                type="number"
                step="any"
                data-testid={`${testPrefix}-${field}`}
                value={current === undefined ? '' : String(current)}
                onChange={(e: any) => setField(field, e.target.value)}
                className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
              />
            )}
            {field === 'organ' && (
              <Select
                aria-label={label}
                value={context.organ ?? NONE}
                onValueChange={(v) => setField('organ', v)}
                options={[
                  { value: NONE, label: '—' },
                  ...ORGANS.map((o) => ({ value: o, label: t(`wizard.observation.organ.${o}`) })),
                ]}
              />
            )}
            {(field === 'structure' || field === 'enclosure') && (
              <Select
                aria-label={label}
                value={context[field] ?? NONE}
                onValueChange={(v) => setField(field, v)}
                options={[
                  { value: NONE, label: '—' },
                  ...features
                    .filter((f) => (field === 'enclosure' ? /Greenhouse/.test(f.type) : true))
                    .map((f) => ({ value: f.id, label: `${f.name} (${f.type})` })),
                ]}
              />
            )}
          </div>
        );
      })}
      {errors.map((code) => (
        <p key={code} className="text-xs text-nkz-danger-strong" role="alert">
          {t(`wizard.observation.error.${code}`)}
        </p>
      ))}
    </div>
  );
}

interface FeatureSelectProps {
  label: string;
  value: string | undefined;
  features: FeatureOption[];
  noneLabel: string;
  onChange: (urn: string | undefined) => void;
}

function FeatureSelect({ label, value, features, noneLabel, onChange }: FeatureSelectProps) {
  const known = !value || features.some((f) => f.id === value);
  return (
    <Select
      aria-label={label}
      value={value || NONE}
      onValueChange={(v) => onChange(v === NONE ? undefined : v)}
      options={[
        { value: NONE, label: noneLabel },
        ...(known ? [] : [{ value: value as string, label: value as string }]),
        ...features.map((f) => ({ value: f.id, label: `${f.name} (${f.type})` })),
      ]}
    />
  );
}

/** Local datetime-local text <-> ISO-8601 with the browser offset. */
function toLocalInput(iso: string | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(text: string): string | undefined {
  if (!text) return undefined;
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) return undefined;
  const offset = -d.getTimezoneOffset();
  const sign = offset >= 0 ? '+' : '-';
  const pad = (n: number) => String(Math.trunc(n)).padStart(2, '0');
  return `${text}:00${sign}${pad(Math.abs(offset) / 60)}:${pad(Math.abs(offset) % 60)}`;
}

/**
 * What a sensor observes: medium and where in it, the observed feature, mobility,
 * an installed-since date and optional per-channel overrides. Client-side checks are
 * hints; the server validates again.
 */
export function ObservationFields({ value, onChange, profileAttributes, parcelIds }: ObservationFieldsProps) {
  const { t } = useTranslation();
  const features = useFeatureOptions(parcelIds);
  const [showOverrides, setShowOverrides] = useState(false);
  const context = value.observationContext;
  const medium = context?.medium;
  const nowLocal = useMemo(() => toLocalInput(new Date().toISOString()), []);

  const patch = (changes: Partial<ObservationValue>) => onChange({ ...value, ...changes });

  const setOverride = (attr: string, change: Partial<ChannelOverride>) => {
    const next = { ...(value.channelOverrides ?? {}) };
    const merged: ChannelOverride = { ...(next[attr] ?? {}), ...change };
    if (!merged.observesFeature) delete merged.observesFeature;
    if (!merged.observationContext) delete merged.observationContext;
    if (Object.keys(merged).length === 0) delete next[attr];
    else next[attr] = merged;
    patch({ channelOverrides: next });
  };

  return (
    <div className="space-y-4" data-testid="observation-fields">
      <div>
        <h4 className="text-sm font-semibold text-nkz-text-primary">{t('wizard.observation.title')}</h4>
        <p className="text-xs text-nkz-muted">{t('wizard.observation.hint')}</p>
      </div>

      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.observation.medium')}
        </label>
        <Select
          aria-label={t('wizard.observation.medium')}
          value={medium ?? NONE}
          onValueChange={(v) => {
            if (v === NONE) patch({ observationContext: undefined });
            else if (isMedium(v)) patch({ observationContext: contextForMedium(v, context) });
          }}
          options={[
            { value: NONE, label: t('wizard.observation.medium_unset') },
            ...MEDIA.map((m) => ({ value: m, label: t(`wizard.observation.medium_${m}`) })),
          ]}
        />
      </div>

      {context && (
        <ContextEditor
          testPrefix="obs"
          context={context}
          features={features}
          onChange={(observationContext) => patch({ observationContext })}
        />
      )}

      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.observation.feature')}
        </label>
        <FeatureSelect
          label={t('wizard.observation.feature')}
          value={value.observesFeature}
          features={features}
          noneLabel={t('wizard.observation.feature_none')}
          onChange={(observesFeature) => patch({ observesFeature })}
        />
        <p className="text-xs text-nkz-muted mt-1">
          {t(medium === 'equipment' ? 'wizard.observation.feature_hint_equipment' : 'wizard.observation.feature_hint')}
        </p>
      </div>

      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.observation.mobility')}
        </label>
        <Select
          aria-label={t('wizard.observation.mobility')}
          value={value.mobility ?? NONE}
          onValueChange={(v) => patch({ mobility: v === 'fixed' || v === 'mobile' ? v : undefined })}
          options={[
            { value: NONE, label: t('wizard.observation.mobility_unset') },
            { value: 'fixed', label: t('wizard.observation.mobility_fixed') },
            { value: 'mobile', label: t('wizard.observation.mobility_mobile') },
          ]}
        />
      </div>

      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">
          {t('wizard.observation.valid_from')}
        </label>
        <Input
          type="datetime-local"
          data-testid="obs-validFrom"
          max={nowLocal}
          value={toLocalInput(value.validFrom)}
          onChange={(e: any) => patch({ validFrom: fromLocalInput(e.target.value) })}
          className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
        />
        <p className="text-xs text-nkz-muted mt-1">{t('wizard.observation.valid_from_hint')}</p>
      </div>

      {profileAttributes.length > 0 && (
        <div className="border border-nkz-border rounded-lg">
          <button
            type="button"
            data-testid="obs-overrides-toggle"
            aria-expanded={showOverrides}
            onClick={() => setShowOverrides((open) => !open)}
            className="w-full flex items-center justify-between px-3 py-2 text-sm font-semibold text-nkz-text-primary"
          >
            <span>{t('wizard.observation.overrides_title')}</span>
            <span className="text-xs text-nkz-muted">{showOverrides ? '▲' : '▼'}</span>
          </button>
          {showOverrides && (
            <div className="p-3 space-y-3 border-t border-nkz-border">
              <p className="text-xs text-nkz-muted">{t('wizard.observation.overrides_hint')}</p>
              {profileAttributes.map((attr) => {
                const override = value.channelOverrides?.[attr] ?? {};
                const overrideMedium = override.observationContext?.medium;
                return (
                  <div
                    key={attr}
                    data-testid={`obs-override-${attr}`}
                    className="border border-nkz-border rounded-lg p-3 bg-nkz-surface space-y-2"
                  >
                    <h5 className="text-sm font-semibold text-nkz-text-primary font-mono">{attr}</h5>
                    <Select
                      aria-label={`${attr} ${t('wizard.observation.medium')}`}
                      value={overrideMedium ?? NONE}
                      onValueChange={(v) => {
                        if (v === NONE) setOverride(attr, { observationContext: undefined });
                        else if (isMedium(v)) {
                          setOverride(attr, { observationContext: contextForMedium(v, override.observationContext) });
                        }
                      }}
                      options={[
                        { value: NONE, label: t('wizard.observation.override_same') },
                        ...MEDIA.map((m) => ({ value: m, label: t(`wizard.observation.medium_${m}`) })),
                      ]}
                    />
                    {override.observationContext && (
                      <ContextEditor
                        testPrefix={`obs-${attr}`}
                        context={override.observationContext}
                        features={features}
                        onChange={(observationContext) => setOverride(attr, { observationContext })}
                      />
                    )}
                    <FeatureSelect
                      label={`${attr} ${t('wizard.observation.feature')}`}
                      value={override.observesFeature}
                      features={features}
                      noneLabel={t('wizard.observation.override_same')}
                      onChange={(observesFeature) => setOverride(attr, { observesFeature })}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
