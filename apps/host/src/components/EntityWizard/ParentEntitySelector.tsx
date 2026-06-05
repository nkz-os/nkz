import React, { useState, useEffect } from 'react';
import { Search, MapPin, Building, Sprout } from 'lucide-react';
import api from '@/services/api';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


export interface ParentEntity {
/* eslint-disable @typescript-eslint/no-explicit-any */
  id: string;
  type: string;
  name: string;
  geometry: any; // GeoJSON Polygon or MultiPolygon
}

interface ParentEntitySelectorProps {
  selectedParentId?: string;
  onSelect: (parent: ParentEntity | null) => void;
  entityType?: string; // Optional filter by entity type
  disabled?: boolean;
}

export const ParentEntitySelector: React.FC<ParentEntitySelectorProps> = ({
  selectedParentId,
  onSelect,
  entityType,
  disabled = false
}) => {
  const [parents, setParents] = useState<ParentEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [isOpen, setIsOpen] = useState(false);

  useEffect(() => {
    loadParents();
  }, [entityType]);

  const loadParents = async () => {
    setLoading(true);
    try {
      const entities = await api.getParentEntities(entityType);
      setParents(entities);
    } catch (error) {
      logger.error('Error loading parent entities:', error);
      setParents([]);
    } finally {
      setLoading(false);
    }
  };

  const filteredParents = parents.filter(parent =>
    parent.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
    parent.type.toLowerCase().includes(searchTerm.toLowerCase())
  );

  const selectedParent = parents.find(p => p.id === selectedParentId);

  const getEntityIcon = (type: string) => {
    if (type.includes('Parcel') || type.includes('Vineyard') || type.includes('Olive')) {
      return <Sprout className="w-4 h-4" />;
    }
    if (type.includes('Building')) {
      return <Building className="w-4 h-4" />;
    }
    return <MapPin className="w-4 h-4" />;
  };

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-gray-700">
        Create as subdivision of...
      </label>
      
      <div className="relative">
        <Button
          type="button"
          onClick={() => !disabled && setIsOpen(!isOpen)}
          disabled={disabled}
          className={`w-full px-4 py-2 text-left border rounded-lg bg-white flex items-center justify-between ${
            disabled ? 'bg-nkz-bg-secondary cursor-not-allowed' : 'hover:border-green-500'
          } ${selectedParent ? 'border-green-500' : 'border-nkz-border'}`}
        >
          <span className="flex items-center gap-2">
            {selectedParent ? (
              <>
                {getEntityIcon(selectedParent.type)}
                <span className="font-medium">{selectedParent.name}</span>
                <span className="text-xs text-nkz-muted">({selectedParent.type})</span>
              </>
            ) : (
              <span className="text-nkz-muted">Select parent entity (optional)</span>
            )}
          </span>
          <span className="text-xs text-nkz-muted">▼</span>
        </Button>

        {isOpen && !disabled && (
          <div className="absolute z-50 w-full mt-1 bg-white border border-nkz-border rounded-lg shadow-lg max-h-60 overflow-auto">
            <div className="p-2 border-b">
              <div className="relative">
                <Search className="absolute left-2 top-2.5 w-4 h-4 text-nkz-muted" />
                <Input
                  type="text"
                  placeholder="Search parent entities..."
                  value={searchTerm}
                  onChange={(e: any) => setSearchTerm(e.target.value)}
                  className="w-full pl-8 pr-2 py-1.5 text-sm border border-nkz-border rounded focus:ring-2 focus:ring-green-500"
                  autoFocus
                />
              </div>
            </div>
            
            <div className="p-1">
              <Button
                type="button"
                onClick={() => {
                  onSelect(null);
                  setIsOpen(false);
                  setSearchTerm('');
                }}
                className={`w-full px-3 py-2 text-left text-sm rounded hover:bg-nkz-bg-secondary ${
                  !selectedParentId ? 'bg-nkz-success-light text-nkz-success' : ''
                }`}
              >
                <span className="font-medium">None (independent entity)</span>
              </Button>
            </div>

            {loading ? (
              <div className="p-4 text-center text-sm text-nkz-muted">Loading...</div>
            ) : filteredParents.length === 0 ? (
              <div className="p-4 text-center text-sm text-nkz-muted">
                {searchTerm ? 'No matching entities found' : 'No parent entities available'}
              </div>
            ) : (
              filteredParents.map((parent) => (
                <Button
                  key={parent.id}
                  type="button"
                  onClick={() => {
                    onSelect(parent);
                    setIsOpen(false);
                    setSearchTerm('');
                  }}
                  className={`w-full px-3 py-2 text-left text-sm rounded hover:bg-nkz-bg-secondary flex items-center gap-2 ${
                    selectedParentId === parent.id ? 'bg-nkz-success-light text-nkz-success' : ''
                  }`}
                >
                  {getEntityIcon(parent.type)}
                  <div className="flex-1">
                    <div className="font-medium">{parent.name}</div>
                    <div className="text-xs text-nkz-muted">{parent.type}</div>
                  </div>
                </Button>
              ))
            )}
          </div>
        )}
      </div>

      {selectedParent && (
        <p className="text-xs text-nkz-muted">
          The new entity will be created as a subdivision of "{selectedParent.name}".
          Its geometry must be completely within the parent's boundaries.
        </p>
      )}
    </div>
  );
};
