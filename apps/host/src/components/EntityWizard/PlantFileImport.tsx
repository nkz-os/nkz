import { useState } from 'react';
import { Button } from '@nekazari/ui-kit';
import api from '@/services/api';
import { useI18n } from '@/context/I18nContext';
import type { PlantsPreview } from './submission/plants';

type Format = 'gpx' | 'kml' | 'kmz' | 'geojson' | 'csv';

export function fileFormat(name: string): Format | null {
  const ext = name.toLowerCase().split('.').pop() ?? '';
  if (ext === 'json') return 'geojson';
  return (['gpx', 'kml', 'kmz', 'geojson', 'csv'] as const).find((f) => f === ext) ?? null;
}

async function readAs(file: File, format: Format): Promise<string> {
  const buf = typeof file.arrayBuffer === 'function' ? await file.arrayBuffer() : await new Response(file).arrayBuffer();
  const bytes = new Uint8Array(buf);
  if (format === 'csv') return new TextDecoder().decode(bytes);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** Import plants from a GPX/KML/KMZ/GeoJSON/CSV file: preview, then confirm. */
function serverMessage(e: unknown): string | null {
  const msg = (e as { response?: { data?: { error?: unknown } } })?.response?.data?.error;
  return typeof msg === 'string' && msg ? msg : null;
}

export function PlantFileImport({ parcelId, onImported }: { parcelId: string; onImported?: () => void }) {
  const { t } = useI18n();
  const [source, setSource] = useState<{ format: Format; content: string } | null>(null);
  const [preview, setPreview] = useState<PlantsPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const [written, setWritten] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const onFile = async (file: File) => {
    setError(null); setDetail(null); setWritten(null); setPreview(null); setSource(null);
    const format = fileFormat(file.name);
    if (!format) { setError('wizard.plants.file_unsupported'); return; }
    setBusy(true);
    try {
      const file_ = { format, content: await readAs(file, format) };
      setPreview(await api.previewPlants({ parcel_id: parcelId, file: file_ }));
      setSource(file_);
    } catch (e) {
      setError('wizard.plants.preview_failed');
      setDetail(serverMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!source) return;
    setBusy(true); setError(null);
    try {
      const res = await api.createPlants({ parcel_id: parcelId, file: source });
      setWritten(res.written?.length ?? 0);
      setPreview(null); setSource(null);
      if (!res.rejected?.length) onImported?.();
    } catch (e) {
      setError('wizard.plants.import_failed');
      setDetail(serverMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-2 border-t border-nkz-border pt-3">
      <label htmlFor="plant-file" className="block text-sm font-medium text-nkz-text-primary">
        {t('wizard.plants.file_label')}
      </label>
      <input id="plant-file" type="file" accept=".gpx,.kml,.kmz,.geojson,.json,.csv" disabled={busy}
        className="block w-full text-sm"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void onFile(f); e.target.value = ''; }} />
      <p className="text-xs text-nkz-text-secondary">{t('wizard.plants.file_hint')}</p>
      {error && <p className="text-sm text-nkz-danger" role="alert">{t(error)}{detail ? ` (${detail})` : ''}</p>}
      {written !== null && <p className="text-sm text-nkz-success" role="status">{t('wizard.plants.imported', { count: written })}</p>}
      {preview && (
        <div className="space-y-2 text-sm">
          <p>{t('wizard.plants.preview_counts', {
            created: preview.accepted.filter((a) => a.action === 'create').length,
            updated: preview.accepted.filter((a) => a.action === 'update').length,
            rejected: preview.rejected.length,
          })}</p>
          {preview.rejected.length > 0 && (
            <ul className="text-xs text-nkz-danger" data-testid="plant-file-rejected">
              {preview.rejected.map((r, i) => (
                <li key={i}>{t('wizard.plants.rejected_item', {
                  code: r.plantCode ?? (r.index !== null ? `#${r.index + 1}` : '—'),
                  reason: t(`wizard.plants.reasons.${r.reason}`, { field: r.field ?? '', distance: r.distance_m ?? '', other: r.other ?? '' }),
                })}</li>
              ))}
            </ul>
          )}
          {preview.warnings.length > 0 && (
            <ul className="text-xs text-nkz-text-secondary">
              {preview.warnings.map((w, i) => (
                <li key={i}>{t(`wizard.plants.warnings.${w.code}`, { code: w.plantCode, other: w.other ?? '', distance: w.distance_m ?? '' })}</li>
              ))}
            </ul>
          )}
          <Button variant="primary" size="sm" disabled={busy || preview.accepted.length === 0} onClick={() => { void confirm(); }}>
            {t('wizard.plants.import_confirm')}
          </Button>
        </div>
      )}
    </div>
  );
}
