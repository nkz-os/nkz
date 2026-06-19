import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useGeocoder } from '@/hooks/useGeocoder';
import type { GeocodeResult } from '@/types/geocode';

export function MapSearchLupa({ onPick }: { onPick: (r: GeocodeResult) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const { results, loading, error, search } = useGeocoder();

  if (!open) {
    return (
      <button
        aria-label={t('map.search_open', 'Search location')}
        className="text-nkz-fg"
        onClick={() => setOpen(true)}
      >
        <span aria-hidden>🔍</span>
      </button>
    );
  }

  return (
    <div className="text-nkz-fg">
      <input
        role="textbox"
        autoFocus
        value={q}
        placeholder={t('map.search_placeholder') as string}
        onChange={(e) => { setQ(e.target.value); search(e.target.value); }}
        onKeyDown={(e) => { if (e.key === 'Escape') { setOpen(false); setQ(''); } }}
      />
      {loading && <div className="text-nkz-muted">...</div>}
      {error && <div className="text-nkz-muted">{t('map.search_error')}</div>}
      {!error && !loading && q && results.length === 0 && (
        <div className="text-nkz-muted">{t('map.search_no_results')}</div>
      )}
      <ul>
        {results.map((r, i) => (
          <li key={i}>
            <button
              className="text-nkz-fg"
              onClick={() => { onPick(r); setOpen(false); setQ(''); }}
            >
              {r.label}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
