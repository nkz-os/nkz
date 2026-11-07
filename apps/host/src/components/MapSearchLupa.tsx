import { useState, useRef, useEffect, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { useGeocoder } from '@/hooks/useGeocoder';
import type { GeocodeResult } from '@/types/geocode';
import { Search, MapPin, X, Loader2 } from 'lucide-react';

export function MapSearchLupa({ onPick, onClear, onActiveChange, getCameraBias }: {
  onPick: (r: GeocodeResult) => void;
  onClear?: () => void;
  onActiveChange?: (active: boolean) => void;
  getCameraBias?: () => { lat: number; lon: number } | null;
}) {
  const { t, i18n: i18nInstance } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [picked, setPicked] = useState<GeocodeResult | null>(null);
  const [activeIndex, setActiveIndex] = useState(-1);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const activeRef = useRef<HTMLButtonElement>(null);
  // Keep the callback in a ref so a new inline identity cannot retrigger the effect.
  const onActiveChangeRef = useRef(onActiveChange);
  useEffect(() => { onActiveChangeRef.current = onActiveChange; }, [onActiveChange]);

  const { results, loading, error, search, clear } = useGeocoder({
    lang: i18nInstance.language,
    getCameraBias
  });

  const collapseInput = () => {
    setOpen(false);
    setQ('');
    clear();
    setActiveIndex(-1);
  };

  const closeAndClear = () => {
    collapseInput();
    if (picked !== null) {
      setPicked(null);
      onClear?.();
    }
  };

  const selectResult = (r: GeocodeResult) => {
    setPicked(r);
    collapseInput();
    onPick(r);
  };

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    onActiveChangeRef.current?.(open || picked !== null);
  }, [open, picked]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        if (q === '') {
          setOpen(false);
        }
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [q]);

  useEffect(() => {
    if (activeIndex >= 0 && activeRef.current) {
      activeRef.current.scrollIntoView?.({
        behavior: 'smooth',
        block: 'nearest'
      });
    }
  }, [activeIndex]);

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') {
      closeAndClear();
      return;
    }

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex(prev => Math.min(prev + 1, results.length - 1));
      return;
    }

    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(prev => Math.max(prev - 1, 0));
      return;
    }

    if (e.key === 'Enter' && results.length > 0) {
      const selectedIndex = activeIndex >= 0 ? activeIndex : 0;
      selectResult(results[selectedIndex]);
    }
  };

  if (!open) {
    if (picked) {
      return (
        <div className="flex items-center gap-1.5 pl-3 pr-2 h-10 rounded-nkz-lg bg-nkz-surface-raised border border-nkz-border shadow-lg text-nkz-text-primary">
          <button
            type="button"
            aria-label={t('map.search_open')}
            className="flex items-center gap-2 min-w-0 hover:text-nkz-accent-base transition-colors"
            onClick={() => setOpen(true)}
          >
            <MapPin className="w-4 h-4 text-nkz-accent-base shrink-0" />
            <span className="text-sm font-medium truncate max-w-[14rem]">
              {picked.name || picked.label}
            </span>
          </button>
          <button
            type="button"
            aria-label={t('map.search_clear')}
            className="p-1 text-nkz-text-muted hover:text-nkz-text-primary transition-colors"
            onClick={() => {
              setPicked(null);
              onClear?.();
            }}
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      );
    }

    return (
      <button
        aria-label={t('map.search_open')}
        className="flex items-center justify-center w-10 h-10 rounded-nkz-lg bg-nkz-surface-raised border border-nkz-border shadow-lg text-nkz-text-primary hover:bg-nkz-surface-sunken transition-colors"
        onClick={() => setOpen(true)}
      >
        <Search className="w-5 h-5" />
      </button>
    );
  }

  return (
    <div ref={containerRef} className="w-[min(26rem,calc(100vw-3rem))]">
      <div className="flex items-center gap-2 px-3 py-2 bg-nkz-surface-raised border border-nkz-border rounded-nkz-lg shadow-lg focus-within:ring-2 focus-within:ring-nkz-accent-base">
        <Search className="w-4 h-4 shrink-0 text-nkz-text-muted" aria-hidden />
        <input
          ref={inputRef}
          autoFocus
          value={q}
          aria-label={t('map.search_placeholder')}
          placeholder={t('map.search_placeholder')}
          className="flex-1 bg-transparent text-sm text-nkz-text-primary placeholder:text-nkz-text-muted outline-none"
          autoComplete="off"
          onChange={(e) => {
            setQ(e.target.value);
            search(e.target.value);
            setActiveIndex(-1);
          }}
          onKeyDown={handleKeyDown}
        />
        {loading && (
          <Loader2 className="w-4 h-4 text-nkz-text-muted animate-spin" />
        )}
        {q && !loading && (
          <button
            aria-label={t('map.search_clear')}
            className="text-nkz-text-muted hover:text-nkz-text-primary transition-colors"
            onClick={() => {
              setQ('');
              clear();
              setActiveIndex(-1);
            }}
          >
            <X className="w-4 h-4" />
          </button>
        )}
        <button
          aria-label={t('map.search_close')}
          className="text-nkz-text-muted hover:text-nkz-text-primary transition-colors"
          onClick={closeAndClear}
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <div aria-live="polite">
        {error && (
          <div className="px-3 py-2 text-sm text-nkz-danger-strong">
            {t('map.search_error')}
          </div>
        )}
        {!error && !loading && q.length >= 2 && results.length === 0 && (
          <div className="px-3 py-2 text-sm text-nkz-text-muted">
            {t('map.search_no_results')}
          </div>
        )}
      </div>

      {results.length > 0 && (
        <div
          role="listbox"
          aria-label={t('map.search_open')}
          className="mt-1.5 bg-nkz-surface-raised border border-nkz-border rounded-nkz-lg shadow-lg overflow-hidden divide-y divide-nkz-border"
        >
          {results.map((r, i) => (
            <button
              key={`${r.lat},${r.lon},${i}`}
              ref={i === activeIndex ? activeRef : undefined}
              role="option"
              aria-selected={i === activeIndex}
              className={`w-full flex items-start gap-2.5 px-3 py-2.5 text-left hover:bg-nkz-accent-soft transition-colors ${i === activeIndex ? 'bg-nkz-accent-soft' : ''}`}
              onClick={() => selectResult(r)}
              onMouseEnter={() => setActiveIndex(i)}
            >
              <MapPin className={`w-4 h-4 mt-0.5 shrink-0 ${i === activeIndex ? 'text-nkz-accent-base' : 'text-nkz-text-muted'}`} />
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate text-sm text-nkz-text-primary">
                  {r.name || r.label}
                </div>
                {r.secondary && (
                  <div className="text-xs text-nkz-text-muted truncate mt-0.5">
                    {r.secondary}
                  </div>
                )}
              </div>
              <span className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded-nkz-md ${i === activeIndex ? 'bg-nkz-accent-soft text-nkz-accent-strong' : 'bg-nkz-surface-sunken text-nkz-text-secondary'} shrink-0 mt-0.5`}>
                {t(`map.search_type_${r.type}`)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
