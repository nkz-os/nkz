// =============================================================================
// Cadastral Parcel Selector Component
// =============================================================================

import React, { useState } from 'react';
import { MapPin, Search, Loader2, Check, AlertCircle } from 'lucide-react';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


interface Polygon {
  type: 'Polygon';
  coordinates: [number, number][][];
}

interface CadastralParcel {
  reference: string; // Reference catastral
  municipality: string;
  province: string;
  area: number; // m²
  use: string; // Agricultural use
  coordinates: {
    lat: number;
    lon: number;
  };
  geometry?: Polygon;
}

interface CadastralSelectorProps {
  onSelect: (parcel: CadastralParcel | null) => void;
  selectedParcel?: CadastralParcel | null;
  disabled?: boolean;
}

export const CadastralSelector: React.FC<CadastralSelectorProps> = ({
  onSelect,
  selectedParcel,
  disabled = false,
}) => {
  const [searchTerm, setSearchTerm] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [results, setResults] = useState<CadastralParcel[]>([]);
  const [error, setError] = useState<string | null>(null);

  const searchCadastral = async (reference: string) => {
    if (reference.length < 14) {
      setResults([]);
      return;
    }

    setIsSearching(true);
    setError(null);

    try {
      // Catastro API via nkz-module-cadastral-spain/backend/app/routers/catastro.py
      // For now, return mock data
      const mockResults: CadastralParcel[] = [
        {
          reference,
          municipality: 'Leioa',
          province: 'Vizcaya',
          area: 25000,
          use: 'Rústico - Olivar',
          coordinates: {
            lat: 43.3208,
            lon: -2.9856,
          },
          geometry: {
            type: 'Polygon',
            coordinates: [[
              [-2.9856, 43.3208],
              [-2.9846, 43.3208],
              [-2.9846, 43.3218],
              [-2.9856, 43.3218],
              [-2.9856, 43.3208],
            ]],
          },
        },
      ];

      setResults(mockResults);
      
      if (mockResults.length === 1) {
        // Auto-select if single result
        onSelect(mockResults[0]);
      }
    } catch (err) {
      setError('Error al buscar en el catastro. Por favor, inténtalo de nuevo.');
      logger.error('Cadastral search error:', err);
    } finally {
      setIsSearching(false);
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchTerm.trim()) {
      searchCadastral(searchTerm.trim());
    }
  };

  const handleSelect = (parcel: CadastralParcel) => {
    onSelect(parcel);
    setSearchTerm('');
    setResults([]);
  };

  const handleClear = () => {
    onSelect(null);
    setSearchTerm('');
    setResults([]);
    setError(null);
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border border-nkz-border p-6">
      <div className="flex items-center gap-2 mb-4">
        <MapPin className="w-5 h-5 text-gray-600" />
        <h3 className="text-lg font-semibold text-gray-900">
          Selección Catastral
        </h3>
      </div>

      <form onSubmit={handleSearch} className="mb-4">
        <div className="flex gap-2">
          <div className="flex-1">
            <Input
              type="text"
              value={searchTerm}
              onChange={(e: any) => setSearchTerm(e.target.value)}
              placeholder="Ref. Catastral (ej: 48037A02100034)"
              disabled={disabled || isSearching}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:bg-nkz-bg-secondary disabled:cursor-not-allowed"
              pattern="[0-9]{7}[A-Z]{2}[0-9]{4}"
            />
            <p className="mt-1 text-xs text-nkz-muted">
              Formato: 7 dígitos + 2 letras + 4 dígitos
            </p>
          </div>
          <Button
            type="submit"
            disabled={disabled || isSearching || searchTerm.length < 11}
            className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
          >
            {isSearching ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Buscando...
              </>
            ) : (
              <>
                <Search className="w-4 h-4" />
                Buscar
              </>
            )}
          </Button>
        </div>
      </form>

      {error && (
        <div className="mb-4 p-3 bg-nkz-error-light border border-red-200 rounded-lg flex items-start gap-2">
          <AlertCircle className="w-5 h-5 text-nkz-error flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-800">{error}</p>
        </div>
      )}

      {results.length > 0 && (
        <div className="mb-4 space-y-2">
          {results.map((parcel) => (
            <div
              key={parcel.reference}
              className="p-4 border border-nkz-border rounded-lg hover:border-blue-500 hover:bg-nkz-info-light cursor-pointer transition-colors"
              onClick={() => handleSelect(parcel)}
            >
              <div className="flex items-start justify-between">
                <div>
                  <p className="font-semibold text-gray-900">
                    {parcel.reference}
                  </p>
                  <p className="text-sm text-gray-600">
                    {parcel.municipality}, {parcel.province}
                  </p>
                  <p className="text-sm text-nkz-muted mt-1">
                    {parcel.use} • {(parcel.area / 10000).toFixed(2)} ha
                  </p>
                </div>
                <Check className="w-5 h-5 text-nkz-info" />
              </div>
            </div>
          ))}
        </div>
      )}

      {selectedParcel && (
        <div className="p-4 bg-nkz-success-light border border-green-200 rounded-lg">
          <div className="flex items-start justify-between">
            <div>
              <p className="font-semibold text-green-900">Parcela seleccionada</p>
              <p className="text-sm text-nkz-success">{selectedParcel.reference}</p>
              <p className="text-sm text-nkz-success mt-1">
                {selectedParcel.municipality}, {selectedParcel.province}
              </p>
            </div>
            <Button
              onClick={handleClear}
              className="text-sm text-nkz-success hover:text-green-900 underline"
            >
              Cambiar
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};

