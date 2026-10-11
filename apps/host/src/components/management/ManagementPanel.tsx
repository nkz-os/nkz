import React, { useState, useEffect } from 'react';
import {
/* eslint-disable @typescript-eslint/no-explicit-any */
    Trash2,
    AlertTriangle,
    RotateCcw,
    Settings
} from 'lucide-react';
import api from '@/services/api';
import { openEntityEditor } from '@/components/EntityEditor';
import { useI18n } from '@/context/I18nContext';
import { logger } from '@/utils/logger';
import { Button } from '@nekazari/ui-kit';



interface ManagementPanelProps {
    entity: {
        id: string;
        type: string;
        name: string;
        [key: string]: any;
    };
    onDelete: () => void;
}

export const ManagementPanel: React.FC<ManagementPanelProps> = ({
    entity,
    onDelete
}) => {
    const { t } = useI18n();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
    const [schema, setSchema] = useState<any>(null);

    // Shown read-only; editing goes through the entity editor, which works on the
    // NGSI-LD entity rather than on this list row.
    const [formData, setFormData] = useState<Record<string, any>>({});

    // Load schema to know editable fields
    useEffect(() => {
        const loadSchema = async () => {
            try {
                // Fetch all schemas or try to find one
                const response = await api.get('/sdm/schemas');

                // Handle both array response (find by type) and object response
                let entitySchema = response.data;
                if (Array.isArray(response.data)) {
                    entitySchema = response.data.find((s: any) => s.entityType === entity.type);
                } else if (response.data[entity.type]) {
                    entitySchema = response.data[entity.type];
                }
                setSchema(entitySchema);

            } catch (err) {
                logger.warn('Could not load schema for management:', err);
            }
        };

        if (entity.type) {
            loadSchema();
            // Initialize form data from entity
            const initialData: Record<string, any> = {};
            // Helper to extract value from NGSI-LD format or direct property
            Object.keys(entity).forEach(key => {
                if (key === 'id' || key === 'type') return;

                let val = entity[key];
                // Handle NGSI-LD value structure { type: 'Property', value: ... }
                if (val && typeof val === 'object' && 'value' in val) {
                    val = val.value;
                }
                initialData[key] = val;
            });
            setFormData(initialData);
        }
    }, [entity]);

    const handleDelete = async () => {
        setLoading(true);
        try {
            await api.deleteSDMEntity(entity.type, entity.id);
            if (onDelete) onDelete();
        } catch (err: any) {
            logger.error('Error deleting entity:', err);
            setError(err.response?.data?.error || err.message || 'Error al eliminar');
            setLoading(false);
            setShowDeleteConfirm(false);
        }
    };

    const renderField = (key: string, value: any) => {
        // Skip internal fields
        if (['id', 'type', 'location', 'createdAt', 'modifiedAt', '@context'].includes(key)) return null;

        // Label from the schema when it has one
        const attrSchema = schema?.attributes?.[key];
        const label = attrSchema?.description || key;

        return (
            <div key={key} className="flex flex-col py-2 border-b border-gray-100 last:border-0 hover:bg-nkz-bg-secondary px-2 rounded -mx-2 bg-transparent transition-colors">
                <span className="text-xs font-semibold text-nkz-muted uppercase tracking-wider mb-1">{label}</span>
                <span className="text-gray-800 break-words font-medium">
                    {typeof value === 'boolean' ? (value ? 'Sí' : 'No') : String(value)}
                </span>
            </div>
        );
    };

    return (
        <div className="flex flex-col h-full bg-nkz-bg-secondary">

            {/* Messages Area */}
            {error && (
                <div className="m-4 mb-0 p-3 bg-nkz-danger-soft border border-red-200 rounded-lg flex items-start gap-2 text-sm text-red-800 animate-in fade-in slide-in-from-top-2 duration-200">
                    <AlertTriangle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            {/* Main Content - Attributes */}
            <div className="flex-1 overflow-y-auto p-4">
                <div className="bg-white rounded-xl shadow-sm border border-nkz-border overflow-hidden">
                    <div className="px-4 py-3 bg-nkz-bg-secondary border-b border-nkz-border flex justify-between items-center">
                        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
                            <Settings className="w-4 h-4 text-nkz-muted" />
                            Propiedades
                        </h3>
                        <Button variant="ghost"
                            onClick={() => openEntityEditor(entity.id, entity.type)}
                            className="text-sm text-orange-600 hover:text-orange-700 font-medium px-3 py-1 hover:bg-orange-50 rounded-md transition-colors"
                        >
                            {t('edit')}
                        </Button>
                    </div>

                    <div className="p-4 space-y-1">
                        {/* Always show Name */}
                        {renderField('name', formData.name)}

                        {/* Dynamically render other fields based on available data */}
                        {Object.keys(formData).map(key => {
                            if (key === 'name') return null; // Already handled
                            return renderField(key, formData[key]);
                        })}

                        {Object.keys(formData).length <= 1 && (
                            <p className="text-nkz-muted text-sm italic text-center py-4">
                                Sin propiedades adicionales
                            </p>
                        )}
                    </div>
                </div>
            </div>

            {/* Danger Zone */}
            <div className="p-4 border-t border-nkz-border bg-white">
                {!showDeleteConfirm ? (
                    <Button
                        onClick={() => setShowDeleteConfirm(true)}
                        className="w-full flex items-center justify-center gap-2 px-4 py-3 text-nkz-danger-strong bg-nkz-danger-soft hover:bg-nkz-danger-soft rounded-lg border border-red-200 transition-colors font-medium"
                    >
                        <Trash2 className="w-4 h-4" />
                        Eliminar Entidad
                    </Button>
                ) : (
                    <div className="bg-nkz-danger-soft rounded-lg border border-red-200 p-4 animate-in zoom-in-95 duration-200">
                        <h4 className="font-bold text-red-900 mb-2 flex items-center gap-2">
                            <AlertTriangle className="w-5 h-5" />
                            ¿Estás seguro?
                        </h4>
                        <p className="text-sm text-red-800 mb-4">
                            Esta acción eliminará <strong>{entity.name}</strong> permanentemente. No se puede deshacer.
                        </p>
                        <div className="flex gap-3">
                            <Button
                                onClick={() => setShowDeleteConfirm(false)}
                                className="flex-1 px-3 py-2 bg-white text-gray-700 border border-nkz-border rounded-md hover:bg-nkz-bg-secondary font-medium transition-colors"
                            >
                                Cancelar
                            </Button>
                            <Button
                                onClick={handleDelete}
                                disabled={loading}
                                className="flex-1 px-3 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 shadow-sm font-medium transition-colors flex justify-center items-center gap-2"
                            >
                                {loading ? <RotateCcw className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                                Confirmar
                            </Button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};
