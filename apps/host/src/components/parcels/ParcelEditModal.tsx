// =============================================================================
// Parcel Edit Modal - Enhanced for parcels and zones
// =============================================================================

import React, { useState, useEffect } from 'react';
import { X, MapPin, Info } from 'lucide-react';
import type { Parcel } from '@/types';
import { useNotification } from '@/hooks/useNotification';

interface ParcelEditModalProps {
    isOpen: boolean;
    parcel: Parcel | null;
    parentParcel?: Parcel | null; // For zones, show parent info
    onSave: (parcel: Parcel, updates: Partial<Parcel>) => Promise<void>;
    onCancel: () => void;
}

export const ParcelEditModal: React.FC<ParcelEditModalProps> = ({
    isOpen,
    parcel,
    parentParcel,
    onSave,
    onCancel,
}) => {
    const [formData, setFormData] = useState({
        name: '',
        cropType: '',
        notes: '',
    });
    const [isSaving, setIsSaving] = useState(false);

    useEffect(() => {
        if (parcel) {
            setFormData({
                name: parcel.name || '',
                cropType: parcel.cropType || '',
                notes: parcel.notes || '',
            });
        }
    }, [parcel]);

    if (!isOpen || !parcel) return null;

    const isZone = parcel.category === 'managementZone';

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsSaving(true);
        try {
            await onSave(parcel, formData);
            onCancel();
        } catch (error) {
            console.error('Error saving parcel:', error);
            showNotification({ type: 'error', message: 'Error al guardar los cambios' });
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4">
                <div className="flex items-center justify-between p-4 border-b border-nkz-border">
                    <div>
                        <h2 className="text-xl font-bold text-gray-900">
                            {isZone ? 'Editar Zona' : 'Editar Parcela'}
                        </h2>
                        {isZone && (
                            <p className="text-xs text-nkz-muted mt-1 flex items-center gap-1">
                                <MapPin className="w-3 h-3" />
                                Zona de gestión
                            </p>
                        )}
                    </div>
                    <button
                        onClick={onCancel}
                        className="p-1 text-nkz-muted hover:text-gray-600 hover:bg-nkz-bg-secondary rounded transition-colors"
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>

                {/* Zone Info */}
                {isZone && parentParcel && (
                    <div className="p-4 bg-nkz-info-light border-b border-blue-200">
                        <div className="flex items-start gap-2">
                            <Info className="w-4 h-4 text-nkz-info mt-0.5 flex-shrink-0" />
                            <div className="flex-1">
                                <p className="text-sm font-medium text-blue-900">Parcela Padre</p>
                                <p className="text-xs text-nkz-info">{parentParcel.name || parentParcel.cadastralReference || parentParcel.id}</p>
                                <p className="text-xs text-nkz-info mt-1">
                                    Los cambios en esta zona no afectan a la parcela padre.
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                <form onSubmit={handleSubmit} className="p-4 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                            Nombre
                        </label>
                        <input
                            type="text"
                            value={formData.name}
                            onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                            className="w-full px-3 py-2 border border-nkz-border rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                            placeholder="Nombre de la parcela"
                        />
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                            Tipo de Cultivo
                        </label>
                        <select
                            value={formData.cropType}
                            onChange={(e) => setFormData(prev => ({ ...prev, cropType: e.target.value }))}
                            className="w-full px-3 py-2 border border-nkz-border rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                        >
                            <option value="">Selecciona un cultivo</option>
                            <option value="wheat">Trigo</option>
                            <option value="barley">Cebada</option>
                            <option value="corn">Maíz</option>
                            <option value="sunflower">Girasol</option>
                            <option value="olive">Olivo</option>
                            <option value="vineyard">Vid</option>
                            <option value="vegetables">Hortalizas</option>
                            <option value="fruit">Frutales</option>
                            <option value="other">Otro</option>
                        </select>
                        {isZone && (
                            <p className="text-xs text-nkz-muted mt-1">
                                Nota: Puedes sobrescribir el tipo de cultivo heredado de la parcela padre.
                            </p>
                        )}
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 mb-1">
                            Notas
                        </label>
                        <textarea
                            value={formData.notes}
                            onChange={(e) => setFormData(prev => ({ ...prev, notes: e.target.value }))}
                            className="w-full px-3 py-2 border border-nkz-border rounded-md focus:outline-none focus:ring-2 focus:ring-green-500"
                            rows={3}
                            placeholder="Notas adicionales sobre la parcela"
                        />
                    </div>

                    <div className="flex justify-end gap-2 pt-4 border-t border-nkz-border">
                        <button
                            type="button"
                            onClick={onCancel}
                            className="px-4 py-2 text-gray-700 bg-nkz-bg-secondary rounded-md hover:bg-gray-200 transition-colors"
                            disabled={isSaving}
                        >
                            Cancelar
                        </button>
                        <button
                            type="submit"
                            className="px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 transition-colors disabled:bg-gray-300 disabled:cursor-not-allowed"
                            disabled={isSaving}
                        >
                            {isSaving ? 'Guardando...' : 'Guardar'}
                        </button>
                    </div>
                </form>
            </div>
        </div>
    );
};

