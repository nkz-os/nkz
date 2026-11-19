/* eslint-disable @typescript-eslint/no-explicit-any */
// Deployment history of a device: where / what it observed over time, with two
// actions (installer role; the server enforces it): open a new period ("move /
// change context") or fix an existing one in place ("correct this period").
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Input } from '@nekazari/ui-kit';
import {
  apiErrorMessage,
  correctDeployment,
  listDeviceDeployments,
  listSensorProfiles,
  moveDeviceDeployment,
  type CorrectDeploymentRequest,
  type DeviceDeployment,
  type MoveDeploymentRequest,
} from '@/services/dataloggersApi';
import {
  ObservationFields,
  fromLocalInput,
  toLocalInput,
  type ObservationValue,
} from '../EntityWizard/steps/ObservationFields';
import { relationshipTargets } from '@/utils/entityRelations';
import { contextErrors, type ObservationContext } from '@/utils/observationContext';

interface DeviceDeploymentsProps {
  deviceUrn: string;
  /** Parcels the device measures; limits the choices of the observed feature. */
  parcelIds?: string[];
  /** Profile code of the device (see readDeviceProfileCode); resolves the channels it measures. */
  profileCode?: string | null;
}

/**
 * Profile code of a device: the `profileCode` Property written at registration (the term
 * may come back as a full IRI when it is not in the platform context), else the
 * `hasDeviceProfile` / `refDeviceProfile` URN suffix.
 */
export function readDeviceProfileCode(entity: unknown): string | null {
  if (!entity || typeof entity !== 'object') return null;
  const e = entity as Record<string, unknown>;
  const prop = e.profileCode ?? e['https://nkz-os.org/ns/profileCode'];
  const code = prop && typeof prop === 'object' ? (prop as { value?: unknown }).value : prop;
  if (typeof code === 'string' && code) return code;
  const [urn] = relationshipTargets(e.hasDeviceProfile ?? e.refDeviceProfile);
  if (!urn) return null;
  return urn.startsWith('urn:ngsi-ld:DeviceProfile:') ? urn.slice('urn:ngsi-ld:DeviceProfile:'.length) : urn;
}

type Editor = { kind: 'move' } | { kind: 'correct'; deployment: DeviceDeployment } | null;

const INPUT_CLASS = 'w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg';

function formatInstant(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function toValue(dep: DeviceDeployment | undefined): ObservationValue {
  if (!dep) return {};
  return {
    observesFeature: dep.observesFeature ?? undefined,
    observationContext: dep.observationContext?.medium
      ? (dep.observationContext as unknown as ObservationContext)
      : undefined,
    channelOverrides: (dep.channelOverrides ?? {}) as ObservationValue['channelOverrides'],
    mobility: dep.mobility,
  };
}

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

export function DeviceDeployments({ deviceUrn, parcelIds = [], profileCode = null }: DeviceDeploymentsProps) {
  const { t } = useTranslation();
  const [items, setItems] = useState<DeviceDeployment[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor>(null);
  const [value, setValue] = useState<ObservationValue>({});
  const [validFrom, setValidFrom] = useState<string | undefined>();
  const [validTo, setValidTo] = useState<string | null | undefined>();
  const [reason, setReason] = useState('');
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setItems(await listDeviceDeployments(deviceUrn));
      setLoadError(null);
    } catch (err) {
      setLoadError(apiErrorMessage(err, ''));
    }
  }, [deviceUrn]);

  useEffect(() => {
    void load();
  }, [load]);

  const [profileAttrs, setProfileAttrs] = useState<string[] | null>(null);
  useEffect(() => {
    let active = true;
    setProfileAttrs(null);
    if (!profileCode) return undefined;
    listSensorProfiles()
      .then((profiles) => {
        const found = profiles.find((p) => p.code === profileCode);
        if (active && found?.sdm_attributes?.length) setProfileAttrs(found.sdm_attributes);
      })
      .catch(() => undefined); // falls back to the override keys
    return () => {
      active = false;
    };
  }, [profileCode]);

  const current = items?.find((d) => d.valid_to === null);

  const open = (next: Editor) => {
    setEditor(next);
    setError(null);
    setReason('');
    setValidFrom(undefined);
    setValidTo(undefined);
    if (next?.kind === 'correct') {
      setValue(toValue(next.deployment));
      setValidFrom(next.deployment.valid_from);
      setValidTo(next.deployment.valid_to);
      setLat(next.deployment.location ? String(next.deployment.location.lat) : '');
      setLon(next.deployment.location ? String(next.deployment.location.lon) : '');
    } else {
      const base = current ?? items?.[0];
      setValue(toValue(base));
      setLat(base?.location ? String(base.location.lat) : '');
      setLon(base?.location ? String(base.location.lon) : '');
    }
  };

  // Channels come from the device profile; without it, the ones already overridden.
  const profileAttributes =
    profileAttrs ??
    Object.keys((editor?.kind === 'correct' ? editor.deployment : current)?.channelOverrides ?? {});

  const submit = async () => {
    if (!editor) return;
    const trimmed = reason.trim();
    if (!trimmed) {
      setError(t('entities.assets.deployments.reason_required'));
      return;
    }
    if (value.observationContext) {
      const errs = contextErrors(value.observationContext);
      if (errs.length > 0) {
        setError(t(`wizard.observation.error.${errs[0]}`));
        return;
      }
    }
    let location: { lat: number; lon: number } | undefined;
    {
      const base = editor.kind === 'correct' ? editor.deployment : current ?? items?.[0];
      const fixed = (editor.kind === 'correct'
        ? base?.mobility ?? 'fixed'
        : value.mobility ?? base?.mobility ?? 'fixed') === 'fixed';
      if (lat.trim() === '' && lon.trim() === '') {
        if (fixed) {
          setError(t('entities.assets.deployments.location_required'));
          return;
        }
      } else {
        const la = Number(lat);
        const lo = Number(lon);
        if (lat.trim() === '' || lon.trim() === '' || !Number.isFinite(la) || !Number.isFinite(lo)
            || la < -90 || la > 90 || lo < -180 || lo > 180) {
          setError(t('entities.assets.deployments.location_invalid'));
          return;
        }
        location = { lat: la, lon: lo };
      }
    }
    setBusy(true);
    setError(null);
    try {
      if (editor.kind === 'move') {
        const body: MoveDeploymentRequest = { reason: trimmed };
        if (validFrom) body.validFrom = validFrom;
        const base = current ?? items?.[0];
        if (value.observesFeature) body.observesFeature = value.observesFeature;
        else if (base?.observesFeature) body.observesFeature = null;
        if (value.observationContext) body.observationContext = value.observationContext as any;
        if (value.channelOverrides && Object.keys(value.channelOverrides).length > 0) {
          body.channelOverrides = value.channelOverrides as any;
        }
        if (value.mobility) body.mobility = value.mobility;
        if (location) body.location = location;
        await moveDeviceDeployment(deviceUrn, body);
      } else {
        const before = editor.deployment;
        const body: CorrectDeploymentRequest = { reason: trimmed };
        if (validFrom && !same(validFrom, before.valid_from)) body.validFrom = validFrom;
        if (validTo !== undefined && !same(validTo, before.valid_to)) body.validTo = validTo;
        if ((value.observesFeature ?? null) !== (before.observesFeature ?? null)) {
          body.observesFeature = value.observesFeature ?? null;
        }
        if (value.observationContext && !same(value.observationContext, before.observationContext)) {
          body.observationContext = value.observationContext as any;
        }
        if (value.channelOverrides && !same(value.channelOverrides, before.channelOverrides ?? {})) {
          body.channelOverrides = value.channelOverrides as any;
        }
        if (location && !same(location, before.location ?? null)) body.location = location;
        if (Object.keys(body).length === 1) {
          setError(t('entities.assets.deployments.no_changes'));
          setBusy(false);
          return;
        }
        await correctDeployment(before.id, body);
      }
      setEditor(null);
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, t('entities.assets.deployments.save_error')));
    } finally {
      setBusy(false);
    }
  };

  const medium = (dep: DeviceDeployment) => {
    const m = dep.observationContext?.medium;
    return typeof m === 'string' ? t(`wizard.observation.medium_${m}`) : t('wizard.observation.medium_unset');
  };

  const prefix = editor?.kind === 'move' ? 'move' : 'correct';

  return (
    <div className="space-y-3" data-testid="device-deployments">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-nkz-text-primary">{t('entities.assets.deployments.title')}</h3>
        <Button variant="secondary" data-testid="deployment-move" onClick={() => open({ kind: 'move' })}>
          {t('entities.assets.deployments.move')}
        </Button>
      </div>

      {loadError !== null && (
        <p className="text-sm text-nkz-danger-strong">
          {loadError || t('entities.assets.deployments.load_error')}
        </p>
      )}
      {items && items.length === 0 && (
        <p className="text-sm text-nkz-muted">{t('entities.assets.deployments.empty')}</p>
      )}

      <ul className="divide-y divide-nkz-border">
        {(items ?? []).map((dep) => (
          <li key={dep.id} data-testid="deployment-row" className="py-2 flex items-start justify-between gap-2">
            <div className="text-sm min-w-0">
              <p className="font-medium text-nkz-text-primary">
                {formatInstant(dep.valid_from)} - {dep.valid_to ? formatInstant(dep.valid_to) : t('entities.assets.deployments.open')}
              </p>
              <p className="text-xs text-nkz-muted truncate">
                {medium(dep)}
                {dep.observesFeature ? ` · ${dep.observesFeature}` : ''}
              </p>
            </div>
            <Button variant="ghost" data-testid="deployment-correct" onClick={() => open({ kind: 'correct', deployment: dep })}>
              {t('entities.assets.deployments.correct')}
            </Button>
          </li>
        ))}
      </ul>

      {editor && (
        <div className="border border-nkz-border rounded-lg p-3 space-y-3" data-testid="deployment-editor">
          <h4 className="text-sm font-semibold text-nkz-text-primary">
            {t(editor.kind === 'move' ? 'entities.assets.deployments.move' : 'entities.assets.deployments.correct')}
          </h4>

          <div>
            <label className="block text-sm font-medium text-nkz-text-primary mb-1">
              {t(editor.kind === 'move' ? 'entities.assets.deployments.valid_from_move' : 'entities.assets.deployments.valid_from')}
            </label>
            <Input
              type="datetime-local"
              data-testid={`${prefix}-validFrom`}
              value={toLocalInput(validFrom)}
              onChange={(e: any) => setValidFrom(fromLocalInput(e.target.value))}
              className={INPUT_CLASS}
            />
          </div>

          {editor.kind === 'correct' && (
            <div>
              <label className="block text-sm font-medium text-nkz-text-primary mb-1">
                {t('entities.assets.deployments.valid_to')}
              </label>
              <Input
                type="datetime-local"
                data-testid="correct-validTo"
                value={toLocalInput(validTo ?? undefined)}
                onChange={(e: any) => setValidTo(fromLocalInput(e.target.value) ?? null)}
                className={INPUT_CLASS}
              />
              <p className="text-xs text-nkz-muted mt-1">{t('entities.assets.deployments.valid_to_hint')}</p>
            </div>
          )}

          <div>
              <label className="block text-sm font-medium text-nkz-text-primary mb-1">
                {t('entities.assets.deployments.location')}
              </label>
              <div className="grid grid-cols-2 gap-2">
                <Input
                  type="number"
                  step="any"
                  min={-90}
                  max={90}
                  aria-label={t('entities.assets.deployments.latitude')}
                  placeholder={t('entities.assets.deployments.latitude')}
                  data-testid={`${prefix}-lat`}
                  value={lat}
                  onChange={(e: any) => setLat(e.target.value)}
                  className={INPUT_CLASS}
                />
                <Input
                  type="number"
                  step="any"
                  min={-180}
                  max={180}
                  aria-label={t('entities.assets.deployments.longitude')}
                  placeholder={t('entities.assets.deployments.longitude')}
                  data-testid={`${prefix}-lon`}
                  value={lon}
                  onChange={(e: any) => setLon(e.target.value)}
                  className={INPUT_CLASS}
                />
              </div>
          </div>

          <ObservationFields
            value={value}
            onChange={setValue}
            profileAttributes={profileAttributes}
            parcelIds={parcelIds}
            hide={{ validFrom: true, mobility: editor.kind === 'correct' }}
          />

          <div>
            <label className="block text-sm font-medium text-nkz-text-primary mb-1">
              {t('entities.assets.deployments.reason')} *
            </label>
            <Input
              type="text"
              data-testid={`${prefix}-reason`}
              value={reason}
              onChange={(e: any) => setReason(e.target.value)}
              className={INPUT_CLASS}
            />
          </div>

          {error && <p className="text-sm text-nkz-danger-strong">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setEditor(null)} disabled={busy}>
              {t('entities.assets.assign.cancel')}
            </Button>
            <Button variant="primary" data-testid={`${prefix}-submit`} onClick={submit} disabled={busy}>
              {t('entities.assets.assign.save')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

export default DeviceDeployments;
