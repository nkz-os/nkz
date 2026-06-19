import { useState, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useGeocoder } from '@/hooks/useGeocoder';
import type { GeocodeResult } from '@/types/geocode';

export function MapSearchLupa({ onPick }: { onPick: (r: GeocodeResult) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [activeIndex, setActiveIndex] = useState(-1);
  const { results, loading, error, search } = useGeocoder();
  const activeRef = useRef<HTMLButtonElement | null>(null);

  const closeAndClear = () => {
    setOpen(false);
    setQ('');
    search('');
    setActiveIndex(-1);
  };

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
        autoFocus
        value={q}
        aria-label={t('map.search_placeholder') as string}
        placeholder={t('map.search_placeholder') as string}
        onChange={(e) => { setQ(e.target.value); search(e.target.value); setActiveIndex(-1); }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { closeAndClear(); return; }
          if (e.key === 'ArrowDown') { e.preventDefault(); setActiveIndex(prev => Math.min(prev + 1, results.length - 1)); return; }
          if (e.key === 'ArrowUp') { e.preventDefault(); setActiveIndex(prev => Math.max(prev - 1, 0)); return; }
          if (e.key === 'Enter' && results.length > 0 && activeIndex >= 0) {
            onPick(results[activeIndex]);
            closeAndClear();
          }
        }}
      />
      <div aria-live="polite">
        {loading && <div>...</div>}
        {error && <div className="text-nkz-muted">{t('map.search_error')}</div>}
        {!error && !loading && q && results.length === 0 && (
          <div className="text-nkz-muted">{t('map.search_no_results')}</div>
        )}
      </div>
      {results.length > 0 && (
        <ul role="listbox" aria-label={t('map.search_placeholder') as string}>
          {results.map((r, i) => (
            <li key={i} role="option" aria-selected={i === activeIndex}>
              <button
                ref={i === activeIndex ? activeRef : undefined}
                className={`text-nkz-fg ${i === activeIndex ? 'bg-nkz-primary' : ''}`}
                onClick={() => { onPick(r); closeAndClear(); }}
              >
                {r.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
