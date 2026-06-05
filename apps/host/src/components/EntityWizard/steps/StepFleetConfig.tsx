import { useWizard } from '../WizardContext';
import type { FleetFormData } from '../types';
import { Input } from '@nekazari/ui-kit';

const ROBOT_TYPES = ['Wheeled', 'Tracked', 'Aerial', 'Legged', 'Hybrid'] as const;

export function StepFleetConfig() {
  const { entityType, formData, updateFormData } = useWizard();

  if (!formData || formData.macroCategory !== 'fleet') return null;
  const data = formData as FleetFormData;

  const isRobot = entityType === 'AutonomousMobileRobot';
  const isMachine = entityType === 'ManufacturingMachine';

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">Datos de la unidad</h3>

      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Nombre *</label>
        <Input
          type="text"
          value={data.name}
          onChange={(e: any) => updateFormData({ name: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder={isRobot ? 'Ej: Rover Norte-01' : isMachine ? 'Ej: Fendt 516 #3' : 'Nombre de la unidad'}
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Descripción</label>
        <textarea
          value={data.description ?? ''}
          onChange={(e: any) => updateFormData({ description: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder="Descripción opcional"
          rows={2}
        />
      </div>

      {/* Common: manufacturer + serialNumber */}
      <div className="grid grid-cols-2 gap-3 pt-3 border-t">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Fabricante</label>
          <Input
            type="text"
            value={data.manufacturer ?? ''}
            onChange={(e: any) => updateFormData({ manufacturer: e.target.value })}
            className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
            placeholder={isRobot ? 'Ej: Naio Technologies' : 'Ej: Fendt, John Deere'}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Número de serie</label>
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
        <div className="pt-3 border-t space-y-3">
          <h4 className="text-sm font-medium text-gray-700">Configuración ROS2</h4>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Tipo de robot</label>
            <select
              value={data.robotType ?? ''}
              onChange={(e: any) => updateFormData({ robotType: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              <option value="">-- Selecciona tipo --</option>
              {ROBOT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Namespace ROS2</label>
            <Input
              type="text"
              value={data.rosNamespace ?? ''}
              onChange={(e: any) => updateFormData({ rosNamespace: e.target.value })}
              className="w-full px-3 py-2 border border-nkz-border rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500"
              placeholder="Ej: /robot_norte_01"
            />
            <p className="text-xs text-nkz-muted mt-1">
              Namespace ROS2 único. Las credenciales de red se generan al finalizar.
            </p>
          </div>
        </div>
      )}

      {/* Machine-specific (tractor/implement) */}
      {isMachine && (
        <div className="pt-3 border-t">
          <div className="flex items-center gap-2">
            <Input
              type="checkbox"
              id="isobus"
              checked={data.isobusCompatible ?? false}
              onChange={(e: any) => updateFormData({ isobusCompatible: e.target.checked })}
              className="w-4 h-4 accent-indigo-600"
            />
            <label htmlFor="isobus" className="text-sm font-medium text-gray-700">
              Compatible con ISOBUS (ISO 11783)
            </label>
          </div>
        </div>
      )}
    </div>
  );
}
