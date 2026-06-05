// =============================================================================
// Add Agricultural Machine Modal Component
// =============================================================================
// Modal para registrar maquinaria agrícola (tractores ISOBUS, etc.)
// =============================================================================

import React, { useState } from 'react';
import { X, Save, MapPin, Tractor, AlertCircle } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { getConfig } from '@/config/environment';
import api from '@/services/api';
import type { AgriculturalMachine } from '@/types';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


/* eslint-disable @typescript-eslint/no-explicit-any */
const config = getConfig();

interface AddMachineModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  defaultLocation?: { lat: number; lon: number };
}

export const AddMachineModal: React.FC<AddMachineModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
  defaultLocation
}) => {
  const { t } = useI18n();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formData, setFormData] = useState({
    name: '',
    machine_type: 'tractor' as 'tractor' | 'harvester' | 'sprayer' | 'spreader' | 'planter' | 'tiller',
    operation_type: 'seeding' as 'seeding' | 'fertilization' | 'spraying' | 'harvesting' | 'tillage' | 'irrigation',
    status: 'idle' as 'idle' | 'working' | 'maintenance' | 'error',
    latitude: defaultLocation?.lat || 0,
    longitude: defaultLocation?.lon || 0,
    manufacturer: '',
    model: '',
    serialNumber: '',
    isobusCompatible: false,
    icon2d: '',
    model3d: '',
    notes: ''
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    if (!formData.name) {
      setError(t('machines.required_fields'));
      setLoading(false);
      return;
    }

    if (!formData.latitude || !formData.longitude) {
      setError(t('machines.required_coordinates'));
      setLoading(false);
      return;
    }

    try {
      // Create NGSI-LD entity according to SDM
      const machineData: Record<string, unknown> = {
        id: `urn:ngsi-ld:ManufacturingMachine:${Date.now()}`,
        type: 'ManufacturingMachine',
        category: { type: 'Property', value: 'tractor' },
        name: {
          type: 'Property',
          value: formData.name
        },
        status: {
          type: 'Property',
          value: formData.status
        },
        operationType: {
          type: 'Property',
          value: formData.operation_type
        },
        location: {
          type: 'GeoProperty',
          value: {
            type: 'Point',
            coordinates: [formData.longitude, formData.latitude]
          }
        },
        '@context': [config.external.contextUrl]
      };

      // Add optional fields
      if (formData.manufacturer) {
        machineData.manufacturer = {
          type: 'Property',
          value: formData.manufacturer
        };
      }
      if (formData.model) {
        machineData.model = {
          type: 'Property',
          value: formData.model
        };
      }
      if (formData.serialNumber) {
        machineData.serialNumber = {
          type: 'Property',
          value: formData.serialNumber
        };
      }
      if (formData.isobusCompatible !== undefined) {
        machineData.isobusCompatible = {
          type: 'Property',
          value: formData.isobusCompatible
        };
      }
      if (formData.icon2d) {
        machineData.icon2d = {
          type: 'Property',
          value: formData.icon2d
        };
      }
      if (formData.model3d) {
        machineData.model3d = {
          type: 'Property',
          value: formData.model3d
        };
      }
      if (formData.notes) {
        machineData.notes = {
          type: 'Property',
          value: formData.notes
        };
      }

      await api.createMachine(machineData as Partial<AgriculturalMachine>);
      
      // Reset form
      setFormData({
        name: '',
        machine_type: 'tractor',
        operation_type: 'seeding',
        status: 'idle',
        latitude: defaultLocation?.lat || 0,
        longitude: defaultLocation?.lon || 0,
        manufacturer: '',
        model: '',
        serialNumber: '',
        isobusCompatible: false,
        icon2d: '',
        model3d: '',
        notes: ''
      });

      if (onSuccess) {
        onSuccess();
      }
      onClose();
    } catch (error: unknown) {
      logger.error('Error saving machine:', error);
      const ax = error as { response?: { data?: { error?: string } } };
      const errorMsg = ax.response?.data?.error || (t('machines.save_error'));
      setError(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="bg-gradient-to-r from-orange-500 to-orange-600 px-6 py-4 flex justify-between items-center sticky top-0 z-10">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Tractor className="w-6 h-6" />
            {t('machines.title')}
          </h2>
          <Button
            onClick={onClose}
            className="text-white hover:text-gray-200 transition"
            disabled={loading}
          >
            <X className="w-6 h-6" />
          </Button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && (
            <div className="bg-nkz-error-light border border-red-200 rounded-lg p-4 flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-nkz-error mt-0.5 flex-shrink-0" />
              <div className="flex-1">
                <p className="text-red-800 font-medium">Error</p>
                <p className="text-nkz-error text-sm">{error}</p>
              </div>
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('machines.name')} *
            </label>
            <Input
              type="text"
              value={formData.name}
              onChange={(e: any) => setFormData({ ...formData, name: e.target.value })}
              placeholder={t('machines.name_placeholder')}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              disabled={loading}
              required
            />
          </div>

          {/* Manufacturer & Model */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('machines.manufacturer')}
              </label>
              <Input
                type="text"
                value={formData.manufacturer}
                onChange={(e: any) => setFormData({ ...formData, manufacturer: e.target.value })}
                placeholder={t('machines.manufacturer_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('machines.model')}
              </label>
              <Input
                type="text"
                value={formData.model}
                onChange={(e: any) => setFormData({ ...formData, model: e.target.value })}
                placeholder={t('machines.model_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
          </div>

          {/* Serial Number */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('machines.serial_number')}
            </label>
            <Input
              type="text"
              value={formData.serialNumber}
              onChange={(e: any) => setFormData({ ...formData, serialNumber: e.target.value })}
              placeholder={t('machines.serial_number_placeholder')}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              disabled={loading}
            />
          </div>

          {/* Status & Operation Type */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('machines.status')}
              </label>
              <select
                value={formData.status}
                onChange={(e: any) =>
                  setFormData({ ...formData, status: e.target.value as typeof formData.status })
                }
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
              >
                <option value="idle">{t('machines.idle')}</option>
                <option value="working">{t('machines.working')}</option>
                <option value="maintenance">{t('machines.maintenance')}</option>
                <option value="error">{t('machines.error')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('machines.operation_type')}
              </label>
              <select
                value={formData.operation_type}
                onChange={(e: any) =>
                  setFormData({
                    ...formData,
                    operation_type: e.target.value as typeof formData.operation_type
                  })
                }
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
              >
                <option value="seeding">{t('machines.op_seeding')}</option>
                <option value="fertilization">{t('machines.op_fertilization')}</option>
                <option value="spraying">{t('machines.op_spraying')}</option>
                <option value="harvesting">{t('machines.op_harvesting')}</option>
                <option value="tillage">{t('machines.op_tillage')}</option>
                <option value="irrigation">{t('machines.op_irrigation')}</option>
              </select>
            </div>
          </div>

          {/* ISOBUS Compatibility */}
          <div className="flex items-center">
            <Input
              type="checkbox"
              id="isobus"
              checked={formData.isobusCompatible}
              onChange={(e: any) => setFormData({ ...formData, isobusCompatible: e.target.checked })}
              className="w-4 h-4 text-orange-600 border-nkz-border rounded focus:ring-orange-500"
              disabled={loading}
            />
            <label htmlFor="isobus" className="ml-2 text-sm text-gray-700">
              {t('machines.isobus_compatible')}
            </label>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 flex items-center gap-1">
                <MapPin className="w-4 h-4" />
                Latitud (GPS) *
              </label>
              <Input
                type="number"
                step="any"
                value={formData.latitude}
                onChange={(e: any) => setFormData({ ...formData, latitude: parseFloat(e.target.value) || 0 })}
                placeholder="Ej: 42.571493"
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 flex items-center gap-1">
                <MapPin className="w-4 h-4" />
                Longitud (GPS) *
              </label>
              <Input
                type="number"
                step="any"
                value={formData.longitude}
                onChange={(e: any) => setFormData({ ...formData, longitude: parseFloat(e.target.value) || 0 })}
                placeholder="Ej: -2.028218"
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
                required
              />
            </div>
          </div>

          {/* Location */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 flex items-center gap-1">
                <MapPin className="w-4 h-4" />
                {t('machines.latitude')} *
              </label>
              <Input
                type="number"
                step="any"
                value={formData.latitude}
                onChange={(e: any) => setFormData({ ...formData, latitude: parseFloat(e.target.value) || 0 })}
                placeholder={t('machines.latitude_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 flex items-center gap-1">
                <MapPin className="w-4 h-4" />
                {t('machines.longitude')} *
              </label>
              <Input
                type="number"
                step="any"
                value={formData.longitude}
                onChange={(e: any) => setFormData({ ...formData, longitude: parseFloat(e.target.value) || 0 })}
                placeholder={t('machines.longitude_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
                required
              />
            </div>
          </div>

          {/* Visual Assets */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('machines.icon2d')}
              </label>
              <Input
                type="url"
                value={formData.icon2d}
                onChange={(e: any) => setFormData({ ...formData, icon2d: e.target.value })}
                placeholder="https://ejemplo.com/icono.png"
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('machines.model3d')}
              </label>
              <Input
                type="url"
                value={formData.model3d}
                onChange={(e: any) => setFormData({ ...formData, model3d: e.target.value })}
                placeholder="https://ejemplo.com/modelo.glb"
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('machines.notes')}
            </label>
            <textarea
              value={formData.notes}
              onChange={(e: any) => setFormData({ ...formData, notes: e.target.value })}
              rows={3}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              placeholder={t('machines.notes_placeholder')}
              disabled={loading}
            />
          </div>

          {/* Buttons */}
          <div className="flex gap-3 pt-4">
            <Button
              type="submit"
              disabled={loading}
              className="flex-1 px-4 py-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 transition flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Save className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              {loading ? (t('machines.saving')) : (t('machines.save'))}
            </Button>
            <Button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {t('common.cancel')}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
};

