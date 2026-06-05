import React, { useState, useEffect, useRef } from 'react';
import { Search, Loader2, X, MapPin } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import api from '@/services/api';
import { getNGSIValue } from '@/types/ngsi-ld';
import type { NGSAttribute } from '@/types/ngsi-ld';
import { Button, Input } from '@nekazari/ui-kit';

interface Props {
  targetType: string;
  currentEntityType: string;
  currentEntityId: string;
  value: NGSAttribute | undefined;
  onChange: (attr: NGSAttribute | undefined) => void;
  labelKey: string;
}

interface SearchResult {
  id: string;
  name: string;
  type: string;
}

export const EntitySearchInput: React.FC<Props> = ({
  targetType, currentEntityType, currentEntityId,
  value, onChange, labelKey,
}) => {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const resolvedTarget = targetType === '<same>' ? currentEntityType : targetType;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const currentObject = value && (value as any).type === 'Relationship'
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ? (value as any).object : undefined;

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  useEffect(() => {
    if (searchTerm.length < 2) { setResults([]); return; }
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const response = await (api as any).client.get('/ngsi-ld/v1/entities', {
          params: { type: resolvedTarget, q: searchTerm, limit: 10 },
        });
        const entities = Array.isArray(response.data) ? response.data : (response.data?.instances || []);
        const items: SearchResult[] = entities
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .filter((e: any) => e.id !== currentEntityId)
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          .map((e: any) => ({
            id: e.id,
            name: getNGSIValue(e.name) || e.id?.split(':')?.pop() || e.id,
            type: e.type || resolvedTarget,
          }));
        setResults(items);
      } catch { setResults([]); }
      finally { setSearching(false); }
    }, 300);
    return () => clearTimeout(timer);
  }, [searchTerm, resolvedTarget, currentEntityId]);

  const handleSelect = (result: SearchResult) => {
    onChange({ type: 'Relationship', object: result.id } as NGSAttribute);
    setOpen(false);
    setSearchTerm('');
  };

  const handleClear = () => {
    onChange(undefined);
  };

  const displayName = currentObject
    ? currentObject.split(':').pop() || currentObject
    : null;

  return (
    <div className="flex flex-col gap-1" ref={ref}>
      <label className="text-xs font-medium text-gray-600">{t(labelKey)}</label>
      {currentObject ? (
        <div className="flex items-center gap-2 px-3 py-2 bg-nkz-bg-secondary border border-nkz-border rounded-lg text-sm">
          <MapPin className="w-3.5 h-3.5 text-nkz-muted" />
          <span className="flex-1 truncate text-gray-700">{displayName}</span>
          <Button onClick={handleClear} className="text-nkz-muted hover:text-nkz-error">
            <X className="w-4 h-4" />
          </Button>
        </div>
      ) : (
        <Button
          onClick={() => setOpen(true)}
          className="px-3 py-2 border border-dashed border-nkz-border rounded-lg text-sm text-nkz-muted hover:border-blue-400 hover:text-nkz-info text-left"
        >
          {t('editor.select_entity') || 'Seleccionar entidad...'}
        </Button>
      )}
      {open && (
        <div className="relative mt-1">
          <div className="flex items-center border border-nkz-border rounded-lg overflow-hidden">
            <Search className="w-4 h-4 text-nkz-muted ml-3 flex-shrink-0" />
            <Input
              type="text"
              value={searchTerm}
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              onChange={(e: any) => setSearchTerm(e.target.value)}
              placeholder={t('editor.search_entity') || 'Buscar...'}
              className="w-full px-3 py-2 text-sm focus:outline-none"
              autoFocus
            />
            {searching && <Loader2 className="w-4 h-4 animate-spin text-nkz-muted mr-3" />}
          </div>
          {results.length > 0 && (
            <div className="absolute z-20 mt-1 w-full max-h-48 overflow-y-auto bg-white border border-nkz-border rounded-lg shadow-lg">
              {results.map(r => (
                <Button
                  key={r.id}
                  onClick={() => handleSelect(r)}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-nkz-info-light flex items-center gap-2 border-b border-gray-100 last:border-b-0"
                >
                  <MapPin className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" />
                  <span className="text-gray-900 truncate">{r.name}</span>
                  <span className="text-xs text-nkz-muted ml-auto flex-shrink-0">{r.type}</span>
                </Button>
              ))}
            </div>
          )}
          {!searching && searchTerm.length >= 2 && results.length === 0 && (
            <div className="absolute z-20 mt-1 w-full p-3 text-sm text-nkz-muted text-center bg-white border border-nkz-border rounded-lg shadow-lg">
              {t('editor.no_results') || 'Sin resultados'}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
