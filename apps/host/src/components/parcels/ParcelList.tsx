// =============================================================================
// Parcel List Component - Enhanced with Hierarchical View
// =============================================================================
// Displays list of parcels with hierarchical grouping (parcels → zones)
// Includes filters and zone management

import React, { useState, useMemo } from 'react';
import { Edit, Trash2, ChevronDown, ChevronRight, MapPin } from 'lucide-react';
import type { Parcel } from '@/types';
import { ParcelAgroStatus } from './ParcelAgroStatus';
import { Button } from '@nekazari/ui-kit';

interface ParcelListProps {
    parcels: Parcel[];
    isLoading: boolean;
    selectedParcelId?: string | null;
    onEdit: (parcel: Parcel) => void;
    onDelete: (parcel: Parcel) => void;
    onDeleteZone?: (zone: Parcel, parent: Parcel) => void;
    onSelect: (parcel: Parcel) => void;
    onRefresh: () => void;
}

type FilterType = 'all' | 'parcels' | 'zones';

interface GroupedParcel {
    parcel: Parcel;
    zones: Parcel[];
    expanded: boolean;
}

export const ParcelList: React.FC<ParcelListProps> = ({
    parcels,
    isLoading,
    selectedParcelId,
    onEdit,
    onDelete,
    onDeleteZone,
    onSelect,
    onRefresh,
}) => {
    const [filter, setFilter] = useState<FilterType>('all');
    const [expandedParcels, setExpandedParcels] = useState<Set<string>>(new Set());

    // Group parcels and zones
    const groupedData = useMemo(() => {
        const parentParcels = parcels.filter(p => p.category !== 'managementZone');
        const zones = parcels.filter(p => p.category === 'managementZone');

        const grouped: GroupedParcel[] = parentParcels.map(parcel => {
            const parcelZones = zones.filter(z => z.refParent === parcel.id);
            return {
                parcel,
                zones: parcelZones,
                expanded: expandedParcels.has(parcel.id),
            };
        });

        // If filter is 'zones', also show orphaned zones (zones without parent in list)
        if (filter === 'zones') {
            const orphanedZones = zones.filter(z => {
                const hasParent = parentParcels.some(p => p.id === z.refParent);
                return !hasParent;
            });
            orphanedZones.forEach(zone => {
                grouped.push({
                    parcel: zone,
                    zones: [],
                    expanded: false,
                });
            });
        }

        return grouped;
    }, [parcels, expandedParcels, filter]);

    // Filter grouped data
    const filteredData = useMemo(() => {
        if (filter === 'all') return groupedData;
        if (filter === 'parcels') return groupedData.filter(g => g.parcel.category !== 'managementZone');
        if (filter === 'zones') {
            // Show parcels with zones, and orphaned zones
            return groupedData.filter(g => g.zones.length > 0 || g.parcel.category === 'managementZone');
        }
        return groupedData;
    }, [groupedData, filter]);

    const toggleExpand = (parcelId: string) => {
        setExpandedParcels(prev => {
            const next = new Set(prev);
            if (next.has(parcelId)) {
                next.delete(parcelId);
            } else {
                next.add(parcelId);
            }
            return next;
        });
    };

    if (isLoading) {
        return (
            <div className="flex items-center justify-center h-full">
                <div className="text-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-green-600 mx-auto mb-4"></div>
                    <p className="text-gray-600">Cargando parcelas...</p>
                </div>
            </div>
        );
    }

    if (parcels.length === 0) {
        return (
            <div className="flex items-center justify-center h-full">
                <div className="text-center max-w-md">
                    <div className="mb-4">
                        <svg
                            className="mx-auto h-16 w-16 text-nkz-muted"
                            fill="none"
                            viewBox="0 0 24 24"
                            stroke="currentColor"
                        >
                            <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7"
                            />
                        </svg>
                    </div>
                    <h3 className="text-lg font-medium text-gray-900 mb-2">
                        No hay parcelas
                    </h3>
                    <p className="text-gray-600 mb-4">
                        Comienza creando tu primera parcela agrícola.
                        Puedes dibujarla manualmente o seleccionarla del catastro.
                    </p>
                    <Button
                        onClick={onRefresh}
                        className="text-nkz-success hover:text-nkz-success font-medium"
                    >
                        Actualizar lista
                    </Button>
                </div>
            </div>
        );
    }

    const renderParcelRow = (parcel: Parcel, isZone: boolean = false, indent: number = 0) => {
        const isSelected = selectedParcelId === parcel.id;
        const isParent = parcel.category !== 'managementZone';
        const zonesCount = isParent ? parcels.filter(p => p.refParent === parcel.id).length : 0;

        return (
            <tr
                key={parcel.id}
                className={`hover:bg-nkz-bg-secondary cursor-pointer transition-colors ${isSelected ? 'bg-nkz-info-light border-l-4 border-blue-500' : ''} ${isZone ? 'bg-nkz-bg-secondary/50' : ''}`}
                onClick={() => onSelect(parcel)}
                style={{ paddingLeft: `${indent * 24}px` }}
            >
                <td className="px-4 py-4 max-w-xs" style={{ paddingLeft: `${indent * 24 + 16}px` }}>
                    <div className="flex items-center gap-2">
                        {isParent && zonesCount > 0 && (
                            <Button
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                onClick={((e: any) => {
                                    e.stopPropagation();
                                    toggleExpand(parcel.id);
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                }) as any}
                                className="text-nkz-muted hover:text-gray-600"
                            >
                                {expandedParcels.has(parcel.id) ? (
                                    <ChevronDown className="w-4 h-4" />
                                ) : (
                                    <ChevronRight className="w-4 h-4" />
                                )}
                            </Button>
                        )}
                        {isZone && <div className="w-4" />}
                        {isZone ? (
                            <MapPin className="w-4 h-4 text-nkz-success" />
                        ) : (
                            <MapPin className="w-4 h-4 text-nkz-info" />
                        )}
                        <div className="flex-1 min-w-0">
                            <div className="text-base font-semibold text-gray-900 truncate" title={parcel.name || parcel.cadastralReference || parcel.id}>
                                {parcel.name || parcel.cadastralReference || 'Parcela sin nombre'}
                            </div>
                            <div className="text-xs text-nkz-muted font-mono truncate max-w-xs mt-1" title={parcel.id}>
                                {parcel.id.length > 40 ? `${parcel.id.substring(0, 40)}...` : parcel.id}
                            </div>
                            {parcel.notes && (
                                <div className="text-xs text-nkz-muted truncate max-w-xs mt-1" title={parcel.notes}>
                                    {parcel.notes}
                                </div>
                            )}
                        </div>
                    </div>
                </td>
                <td className="px-4 py-4 whitespace-nowrap">
                    <div className="text-sm text-gray-900">{parcel.municipality || '-'}</div>
                    <div className="text-sm text-nkz-muted">{parcel.province || '-'}</div>
                </td>
                <td className="px-4 py-4 whitespace-nowrap">
                    <div className="text-sm text-gray-900">{parcel.cropType || '-'}</div>
                </td>
                <td className="px-4 py-4 whitespace-nowrap">
                    <div className="text-sm text-gray-900">
                        {parcel.area?.toFixed(2) || 'N/A'}
                    </div>
                </td>
                <td className="px-4 py-4 whitespace-nowrap">
                    <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                        parcel.category === 'cadastral'
                            ? 'bg-nkz-info-light text-blue-800'
                            : 'bg-nkz-success-light text-green-800'
                    }`}>
                        {parcel.category === 'cadastral' ? 'Catastral' : parcel.category === 'managementZone' ? 'Zona' : 'Parcela'}
                    </span>
                    {isParent && zonesCount > 0 && (
                        <span className="ml-2 px-2 py-0.5 text-xs bg-nkz-bg-secondary text-gray-600 rounded-full">
                            {zonesCount} zona{zonesCount !== 1 ? 's' : ''}
                        </span>
                    )}
                </td>
                <td className="px-4 py-4 whitespace-nowrap">
                    <ParcelAgroStatus parcelId={parcel.id} />
                </td>
                <td className="px-4 py-4 whitespace-nowrap text-right text-sm font-medium">
                    <div className="flex items-center justify-end gap-2 flex-wrap">
                        <Button
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            onClick={((e: any) => {
                                e.stopPropagation();
                                onEdit(parcel);
                            // eslint-disable-next-line @typescript-eslint/no-explicit-any
                            }) as any}
                            className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-nkz-success bg-nkz-success-light rounded-md hover:bg-nkz-success-light transition-colors"
                            title="Editar"
                        >
                            <Edit className="w-4 h-4 mr-1" />
                            Editar
                        </Button>
                        {isZone && onDeleteZone ? (
                            <Button
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                onClick={((e: any) => {
                                    e.stopPropagation();
                                    const parent = parcels.find(p => p.id === parcel.refParent);
                                    if (parent) {
                                        onDeleteZone(parcel, parent);
                                    }
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                }) as any}
                                className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-nkz-error bg-nkz-error-light rounded-md hover:bg-nkz-error-light transition-colors"
                                title="Eliminar zona"
                            >
                                <Trash2 className="w-4 h-4 mr-1" />
                                Eliminar
                            </Button>
                        ) : (
                            <Button
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                onClick={((e: any) => {
                                    e.stopPropagation();
                                    onDelete(parcel);
                                // eslint-disable-next-line @typescript-eslint/no-explicit-any
                                }) as any}
                                className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-nkz-error bg-nkz-error-light rounded-md hover:bg-nkz-error-light transition-colors"
                                title="Eliminar parcela"
                            >
                                <Trash2 className="w-4 h-4 mr-1" />
                                Eliminar
                            </Button>
                        )}
                    </div>
                </td>
            </tr>
        );
    };

    return (
        <div className="w-full" style={{ minHeight: '200px' }}>
            {/* Filter Bar */}
            <div className="mb-4 bg-white p-3 rounded-lg border border-nkz-border flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-700">Filtro:</span>
                    <Button
                        onClick={() => setFilter('all')}
                        className={`px-3 py-1 text-sm rounded-md transition-colors ${
                            filter === 'all'
                                ? 'bg-green-600 text-white'
                                : 'bg-nkz-bg-secondary text-gray-700 hover:bg-gray-200'
                        }`}
                    >
                        Todas ({parcels.length})
                    </Button>
                    <Button
                        onClick={() => setFilter('parcels')}
                        className={`px-3 py-1 text-sm rounded-md transition-colors ${
                            filter === 'parcels'
                                ? 'bg-green-600 text-white'
                                : 'bg-nkz-bg-secondary text-gray-700 hover:bg-gray-200'
                        }`}
                    >
                        Parcelas ({parcels.filter(p => p.category !== 'managementZone').length})
                    </Button>
                    <Button
                        onClick={() => setFilter('zones')}
                        className={`px-3 py-1 text-sm rounded-md transition-colors ${
                            filter === 'zones'
                                ? 'bg-green-600 text-white'
                                : 'bg-nkz-bg-secondary text-gray-700 hover:bg-gray-200'
                        }`}
                    >
                        Zonas ({parcels.filter(p => p.category === 'managementZone').length})
                    </Button>
                </div>
            </div>

            <div className="bg-white rounded-lg shadow overflow-x-auto" style={{ width: '100%' }}>
                <table className="min-w-full divide-y divide-gray-200" style={{ width: '100%', tableLayout: 'auto' }}>
                    <thead className="bg-nkz-bg-secondary">
                        <tr>
                            <th className="px-4 py-3 text-left text-xs font-medium text-nkz-muted uppercase tracking-wider" style={{ maxWidth: '200px' }}>
                                Nombre
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-medium text-nkz-muted uppercase tracking-wider">
                                Municipio
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-medium text-nkz-muted uppercase tracking-wider">
                                Cultivo
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-medium text-nkz-muted uppercase tracking-wider">
                                Área (ha)
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-medium text-nkz-muted uppercase tracking-wider">
                                Tipo
                            </th>
                            <th className="px-4 py-3 text-left text-xs font-medium text-nkz-muted uppercase tracking-wider">
                                Condiciones Agronómicas
                            </th>
                            <th className="px-4 py-3 text-right text-xs font-medium text-nkz-muted uppercase tracking-wider">
                                Acciones
                            </th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {filteredData.map((group) => {
                            const rows: JSX.Element[] = [];
                            
                            // Add parent parcel row
                            rows.push(renderParcelRow(group.parcel, false, 0));
                            
                            // Add zone rows if expanded
                            if (group.expanded && group.zones.length > 0) {
                                group.zones.forEach(zone => {
                                    rows.push(renderParcelRow(zone, true, 1));
                                });
                            }
                            
                            return rows;
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
};
