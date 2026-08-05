// =============================================================================
// Add Livestock Animal Modal Component
// =============================================================================
// Modal para registrar animales de ganado (collares GPS, etc.)
// =============================================================================

import React, { useState } from 'react';
import { X, Save, MapPin, Heart, AlertCircle } from 'lucide-react';
import { useI18n } from '@/context/I18nContext';
import { getConfig } from '@/config/environment';
import api from '@/services/api';
import type { LivestockAnimal } from '@/types';
import { logger } from '@/utils/logger';
import { Button, Input } from '@nekazari/ui-kit';


/* eslint-disable @typescript-eslint/no-explicit-any */
const config = getConfig();

interface AddLivestockModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
  defaultLocation?: { lat: number; lon: number };
}

export const AddLivestockModal: React.FC<AddLivestockModalProps> = ({
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
    species: 'Bos taurus' as 'Bos taurus' | 'Ovis aries' | 'Capra hircus' | 'Sus scrofa' | 'Equus caballus',
    breed: '',
    herdId: '',
    activity: 'grazing' as 'grazing' | 'resting' | 'moving' | 'feeding',
    latitude: defaultLocation?.lat || 0,
    longitude: defaultLocation?.lon || 0,
    birthDate: '',
    weight: '',
    icon2d: '',
    model3d: '',
    notes: ''
  });

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);

    if (!formData.name) {
      setError(t('livestock.required_fields'));
      setLoading(false);
      return;
    }

    if (!formData.latitude || !formData.longitude) {
      setError(t('livestock.required_coordinates'));
      setLoading(false);
      return;
    }

    try {
      // Create NGSI-LD entity according to SDM
      const animalData: Record<string, unknown> = {
        id: `urn:ngsi-ld:LivestockAnimal:${Date.now()}`,
        type: 'LivestockAnimal',
        name: {
          type: 'Property',
          value: formData.name
        },
        species: {
          type: 'Property',
          value: formData.species
        },
        activity: {
          type: 'Property',
          value: formData.activity
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
      if (formData.breed) {
        animalData.breed = {
          type: 'Property',
          value: formData.breed
        };
      }
      if (formData.herdId) {
        animalData.herdId = {
          type: 'Property',
          value: formData.herdId
        };
      }
      if (formData.birthDate) {
        animalData.birthDate = {
          type: 'Property',
          value: formData.birthDate,
          '@type': 'DateTime'
        };
      }
      if (formData.weight) {
        animalData.weight = {
          type: 'Property',
          value: parseFloat(formData.weight),
          unitCode: 'KGM'
        };
      }
      if (formData.icon2d) {
        animalData.icon2d = {
          type: 'Property',
          value: formData.icon2d
        };
      }
      if (formData.model3d) {
        animalData.model3d = {
          type: 'Property',
          value: formData.model3d
        };
      }
      if (formData.notes) {
        animalData.notes = {
          type: 'Property',
          value: formData.notes
        };
      }

      await api.createLivestockAnimal(animalData as Partial<LivestockAnimal>);
      
      // Reset form
      setFormData({
        name: '',
        species: 'Bos taurus',
        breed: '',
        herdId: '',
        activity: 'grazing',
        latitude: defaultLocation?.lat || 0,
        longitude: defaultLocation?.lon || 0,
        birthDate: '',
        weight: '',
        icon2d: '',
        model3d: '',
        notes: ''
      });

      if (onSuccess) {
        onSuccess();
      }
      onClose();
    } catch (error: unknown) {
      logger.error('Error saving livestock:', error);
      const ax = error as { response?: { data?: { error?: string } } };
      const errorMsg = ax.response?.data?.error || (t('livestock.save_error'));
      setError(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        <div className="bg-gradient-to-r from-purple-500 to-purple-600 px-6 py-4 flex justify-between items-center sticky top-0 z-10">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Heart className="w-6 h-6" />
            {t('livestock.title')}
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
            <div className="bg-nkz-danger-soft border border-red-200 rounded-lg p-4 flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-nkz-danger-strong mt-0.5 flex-shrink-0" />
              <div className="flex-1">
                <p className="text-red-800 font-medium">Error</p>
                <p className="text-nkz-danger-strong text-sm">{error}</p>
              </div>
            </div>
          )}

          {/* Name */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('livestock.name')} *
            </label>
            <Input
              type="text"
              value={formData.name}
              onChange={(e: any) => setFormData({ ...formData, name: e.target.value })}
              placeholder={t('livestock.name_placeholder')}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              disabled={loading}
              required
            />
          </div>

          {/* Species & Breed */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.species')} *
              </label>
              <select
                value={formData.species}
                onChange={(e: any) =>
                  setFormData({ ...formData, species: e.target.value as typeof formData.species })
                }
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
                required
              >
                <option value="Bos taurus">{t('livestock.species_cow')}</option>
                <option value="Ovis aries">{t('livestock.species_sheep')}</option>
                <option value="Capra hircus">{t('livestock.species_goat')}</option>
                <option value="Sus scrofa">{t('livestock.species_pig')}</option>
                <option value="Equus caballus">{t('livestock.species_horse')}</option>
              </select>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.breed')}
              </label>
              <Input
                type="text"
                value={formData.breed}
                onChange={(e: any) => setFormData({ ...formData, breed: e.target.value })}
                placeholder={t('livestock.breed_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
          </div>

          {/* Herd ID & Activity */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.herd_id')}
              </label>
              <Input
                type="text"
                value={formData.herdId}
                onChange={(e: any) => setFormData({ ...formData, herdId: e.target.value })}
                placeholder={t('livestock.herd_id_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.activity')}
              </label>
              <select
                value={formData.activity}
                onChange={(e: any) =>
                  setFormData({ ...formData, activity: e.target.value as typeof formData.activity })
                }
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              >
                <option value="grazing">{t('livestock.activity_grazing')}</option>
                <option value="resting">{t('livestock.activity_resting')}</option>
                <option value="moving">{t('livestock.activity_moving')}</option>
                <option value="feeding">{t('livestock.activity_feeding')}</option>
              </select>
            </div>
          </div>

          {/* Birth Date & Weight */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.birth_date')}
              </label>
              <Input
                type="date"
                value={formData.birthDate}
                onChange={(e: any) => setFormData({ ...formData, birthDate: e.target.value })}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.weight')}
              </label>
              <Input
                type="number"
                step="0.1"
                value={formData.weight}
                onChange={(e: any) => setFormData({ ...formData, weight: e.target.value })}
                placeholder={t('livestock.weight_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
          </div>

          {/* Location */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 flex items-center gap-1">
                <MapPin className="w-4 h-4" />
                {t('livestock.latitude')} *
              </label>
              <Input
                type="number"
                step="any"
                value={formData.latitude}
                onChange={(e: any) => setFormData({ ...formData, latitude: parseFloat(e.target.value) || 0 })}
                placeholder={t('livestock.latitude_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
                required
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1 flex items-center gap-1">
                <MapPin className="w-4 h-4" />
                {t('livestock.longitude')} *
              </label>
              <Input
                type="number"
                step="any"
                value={formData.longitude}
                onChange={(e: any) => setFormData({ ...formData, longitude: parseFloat(e.target.value) || 0 })}
                placeholder={t('livestock.longitude_placeholder')}
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
                required
              />
            </div>
          </div>

          {/* Visual Assets */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.icon2d')}
              </label>
              <Input
                type="url"
                value={formData.icon2d}
                onChange={(e: any) => setFormData({ ...formData, icon2d: e.target.value })}
                placeholder="https://ejemplo.com/icono.png"
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                {t('livestock.model3d')}
              </label>
              <Input
                type="url"
                value={formData.model3d}
                onChange={(e: any) => setFormData({ ...formData, model3d: e.target.value })}
                placeholder="https://ejemplo.com/modelo.glb"
                className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
                disabled={loading}
              />
            </div>
          </div>

          {/* Notes */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              {t('livestock.notes')}
            </label>
            <textarea
              value={formData.notes}
              onChange={(e: any) => setFormData({ ...formData, notes: e.target.value })}
              rows={3}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent"
              placeholder={t('livestock.notes_placeholder')}
              disabled={loading}
            />
          </div>

          {/* Buttons */}
          <div className="flex gap-3 pt-4">
            <Button
              type="submit"
              disabled={loading}
              className="flex-1 px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Save className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              {loading ? (t('livestock.saving')) : (t('livestock.save'))}
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

