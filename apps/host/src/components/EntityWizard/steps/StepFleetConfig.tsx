import { useWizard } from '../WizardContext';
import type { FleetFormData } from '../types';
import { Input } from '@nekazari/ui-kit';
import { useI18n } from '@/context/I18nContext';
import { AttributeField } from '@/components/EntityEditor/AttributeField';
import { getSchemasForType } from '@/components/EntityEditor/attributeSchemas';
import type { NGSAttribute } from '@/types/ngsi-ld';

/* eslint-disable @typescript-eslint/no-explicit-any */
const ROBOT_TYPES = ['Wheeled', 'Tracked', 'Aerial', 'Legged', 'Hybrid'] as const;

export function StepFleetConfig() {
  const { entityType, formData, updateFormData } = useWizard();
  const { t } = useI18n();

  if (!formData || formData.macroCategory !== 'fleet') return null;
  const data = formData as FleetFormData;

  const isRobot = entityType === 'AutonomousMobileRobot';
  const isMachine = entityType === 'ManufacturingMachine';

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">{t('wizard.fleet.title')}</h3>

      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">{t('wizard.fields.name_required')}</label>
        <Input
          type="text"
          value={data.name}
          onChange={(e: any) => updateFormData({ name: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder={isRobot ? t('wizard.fleet.name_placeholder_robot') : isMachine ? t('wizard.fleet.name_placeholder_machine') : t('wizard.fleet.name_placeholder')}
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-nkz-text-primary mb-1">{t('wizard.fields.description')}</label>
        <textarea
          value={data.description ?? ''}
          onChange={(e: any) => updateFormData({ description: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder={t('wizard.fields.description_placeholder')}
          rows={2}
        />
      </div>

      {/* Common: manufacturer + serialNumber */}
      <div className="grid grid-cols-2 gap-3 pt-3 border-t border-nkz-border">
        <div>
          <label className="block text-xs font-medium text-nkz-text-secondary mb-1">{t('wizard.fields.manufacturer')}</label>
          <Input
            type="text"
            value={data.manufacturer ?? ''}
            onChange={(e: any) => updateFormData({ manufacturer: e.target.value })}
            className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
            placeholder={isRobot ? t('wizard.fleet.manufacturer_placeholder_robot') : t('wizard.fleet.manufacturer_placeholder_machine')}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-nkz-text-secondary mb-1">{t('wizard.fields.serial_number')}</label>
          <Input
            type="text"
            value={data.serialNumber ?? ''}
            onChange={(e: any) => updateFormData({ serialNumber: e.target.value })}
            className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500"
            placeholder="S/N"
          />
        </div>
      </div>

      {/* Robot-specific */}
      {isRobot && (
        <div className="pt-3 border-t border-nkz-border space-y-3">
          <h4 className="text-sm font-medium text-nkz-text-primary">{t('wizard.fleet.ros2_config')}</h4>

          <div>
            <label className="block text-xs font-medium text-nkz-text-secondary mb-1">{t('wizard.fleet.robot_type')}</label>
            <select
              value={data.robotType ?? ''}
              onChange={(e: any) => updateFormData({ robotType: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 bg-nkz-surface"
            >
              <option value="">{t('wizard.fleet.select_type')}</option>
              {ROBOT_TYPES.map(rt => <option key={rt} value={rt}>{rt}</option>)}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-nkz-text-secondary mb-1">{t('wizard.fleet.ros2_namespace')}</label>
            <Input
              type="text"
              value={data.rosNamespace ?? ''}
              onChange={(e: any) => updateFormData({ rosNamespace: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500"
              placeholder={t('wizard.fleet.ros2_namespace_placeholder')}
            />
            <p className="text-xs text-nkz-muted mt-1">
              {t('wizard.fleet.ros2_namespace_hint')}
            </p>
          </div>
        </div>
      )}

      {/* Machine-specific (tractor/implement) */}
      {isMachine && <MachineGeometryFields data={data} updateFormData={updateFormData} />}
      {isMachine && (
        <div className="pt-3 border-t border-nkz-border">
          <div className="flex items-center gap-2">
            <Input
              type="checkbox"
              id="isobus"
              checked={data.isobusCompatible ?? false}
              onChange={(e: any) => updateFormData({ isobusCompatible: e.target.checked })}
              className="w-4 h-4 accent-indigo-600"
            />
            <label htmlFor="isobus" className="text-sm font-medium text-nkz-text-primary">
              {t('wizard.fleet.isobus_compatible')}
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Role and dimensions of a machine, from the same field list as the entity editor so both
 * ask the same thing. Every dimension is optional; an emptied field leaves no value.
 */
function MachineGeometryFields({ data, updateFormData }: {
  data: FleetFormData;
  updateFormData: (patch: Partial<FleetFormData>) => void;
}) {
  const { t } = useI18n();
  const category = data.category ?? 'tractor';
  const geometry = data.machineGeometry ?? {};
  const categorySchema = getSchemasForType('ManufacturingMachine').find(s => s.key === 'category')!;
  const fields = getSchemasForType('ManufacturingMachine', category).filter(s => s.section === 'kinematic');
  const attr = (value: unknown) => ({ type: 'Property', value }) as NGSAttribute;

  const setDimension = (key: string, value: unknown) => {
    const next = { ...geometry };
    if (value === '' || value === null || value === undefined) delete next[key];
    else next[key] = value as number | string;
    updateFormData({ machineGeometry: next });
  };

  return (
    <div className="pt-3 border-t border-nkz-border space-y-3">
      <AttributeField
        schema={categorySchema}
        value={attr(category)}
        onChange={a => updateFormData({ category: (a?.value || undefined) as FleetFormData['category'] })}
      />
      <h4 className="text-sm font-medium text-nkz-text-primary">{t('wizard.fleet.dimensions')}</h4>
      <p className="text-xs text-nkz-muted">{t('wizard.fleet.dimensions_hint')}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {fields.map(schema => (
          <AttributeField
            key={schema.key}
            schema={schema}
            value={geometry[schema.key] !== undefined ? attr(geometry[schema.key]) : undefined}
            onChange={a => setDimension(schema.key, a?.value)}
          />
        ))}
      </div>
    </div>
  );
}
