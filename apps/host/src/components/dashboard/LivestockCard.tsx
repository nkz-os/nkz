import React from 'react';
import { Heart, Plus } from 'lucide-react';
import type { LivestockAnimal } from '@/types';
import { useI18n } from '@/context/I18nContext';
import { Button } from '@nekazari/ui-kit';

interface LivestockCardProps {
  livestock: LivestockAnimal[];
  canManageDevices: boolean;
  onOpenWizard: (entityType: string) => void;
}

export const LivestockCard: React.FC<LivestockCardProps> = ({ livestock, canManageDevices, onOpenWizard }) => {
  const { t } = useI18n();
  return (
    <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-lg overflow-hidden">
      <div className="bg-gradient-to-r from-purple-500 to-purple-600 px-6 py-4">
        <h2 className="text-xl font-bold text-white flex items-center gap-2">
          <Heart className="w-6 h-6" />
          {t('dashboard.livestock.title')}
        </h2>
      </div>

      <div className="p-6">
        {livestock.length === 0 ? (
          <div className="text-center py-12">
            <Heart className="w-16 h-16 text-gray-300 mx-auto mb-4" />
            <p className="text-nkz-muted mb-4">{t('dashboard.livestock.no_animals')}</p>
            {canManageDevices && (
              <Button
                onClick={() => onOpenWizard('LivestockAnimal')}
                className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition flex items-center gap-2 mx-auto"
              >
                <Plus className="w-4 h-4" />
                {t('dashboard.livestock.add_animal')}
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {livestock.slice(0, 4).map((animal) => (
              <div key={animal.id} className="flex items-center justify-between p-4 bg-nkz-bg-secondary rounded-xl hover:bg-nkz-bg-secondary transition">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-purple-100 text-purple-600 rounded-lg flex items-center justify-center">
                    <Heart className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="font-semibold text-gray-900">
                      {animal.name?.value || animal.id}
                    </h4>
                    <p className="text-sm text-nkz-muted">
                      {typeof animal.species === 'string' ? animal.species : animal.species?.value || t('dashboard.livestock.species_label')}
                    </p>
                  </div>
                </div>
                {animal.activity && (
                  <span className="text-xs px-2 py-1 bg-purple-100 text-purple-700 rounded">
                    {typeof animal.activity === 'string' ? animal.activity : animal.activity.value}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
