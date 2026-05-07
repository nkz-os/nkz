import { useWizard } from '../WizardContext';
import type { FleetFormData } from '../types';

const ROBOT_TYPES = ['Wheeled', 'Tracked', 'Aerial', 'Legged', 'Hybrid'] as const;
const HITCH_TYPES = ['three_point', 'drawbar', 'semi_mounted', 'trailed'] as const;
const STEERING_TYPES = ['ackermann', 'skid_steer', 'crab', 'none'] as const;

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
        <input
          type="text"
          value={data.name}
          onChange={e => updateFormData({ name: e.target.value })}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder={isRobot ? 'Ej: Rover Norte-01' : isMachine ? 'Ej: Fendt 516 #3' : 'Nombre de la unidad'}
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Descripción</label>
        <textarea
          value={data.description ?? ''}
          onChange={e => updateFormData({ description: e.target.value })}
          className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
          placeholder="Descripción opcional"
          rows={2}
        />
      </div>

      {/* Common: manufacturer + serialNumber */}
      <div className="grid grid-cols-2 gap-3 pt-3 border-t">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Fabricante</label>
          <input
            type="text"
            value={data.manufacturer ?? ''}
            onChange={e => updateFormData({ manufacturer: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
            placeholder={isRobot ? 'Ej: Naio Technologies' : 'Ej: Fendt, John Deere'}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">Número de serie</label>
          <input
            type="text"
            value={data.serialNumber ?? ''}
            onChange={e => updateFormData({ serialNumber: e.target.value })}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500"
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
              onChange={e => updateFormData({ robotType: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              <option value="">-- Selecciona tipo --</option>
              {ROBOT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Namespace ROS2</label>
            <input
              type="text"
              value={data.rosNamespace ?? ''}
              onChange={e => updateFormData({ rosNamespace: e.target.value })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500"
              placeholder="Ej: /robot_norte_01"
            />
            <p className="text-xs text-gray-500 mt-1">
              Namespace ROS2 único. Las credenciales de red se generan al finalizar.
            </p>
          </div>
        </div>
      )}

      {/* Machine-specific (tractor/implement) */}
      {isMachine && (
        <div className="pt-3 border-t space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Rol de máquina *</label>
            <select
              value={data.machineRole ?? 'tractor'}
              onChange={e => updateFormData({ machineRole: e.target.value as FleetFormData['machineRole'] })}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
            >
              <option value="tractor">Tractor (unidad de potencia)</option>
              <option value="implement">Apero (implemento)</option>
            </select>
            <p className="text-xs text-gray-500 mt-1">
              Este valor define la categoría SDM usada por GIS Routing para separar tractores y aperos.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="isobus"
              checked={data.isobusCompatible ?? false}
              onChange={e => updateFormData({ isobusCompatible: e.target.checked })}
              className="w-4 h-4 accent-indigo-600"
            />
            <label htmlFor="isobus" className="text-sm font-medium text-gray-700">
              Compatible con ISOBUS (ISO 11783)
            </label>
          </div>

          {(data.machineRole ?? 'tractor') === 'tractor' ? (
            <div className="space-y-3">
              <h4 className="text-sm font-medium text-gray-700">Dimensiones tractor (m)</h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Ancho de vía (trackWidth)</label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={data.trackWidth ?? ''}
                    onChange={e => updateFormData({ trackWidth: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 1.80"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Batalla (wheelbase)</label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={data.wheelbase ?? ''}
                    onChange={e => updateFormData({ wheelbase: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 2.45"
                  />
                </div>
              </div>
              <h5 className="text-xs font-medium text-gray-600">Offset antena GNSS respecto al centro trasero del tractor (m)</h5>
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">gpsOffsetX</label>
                  <input
                    type="number"
                    step="0.01"
                    value={data.gpsOffsetX ?? ''}
                    onChange={e => updateFormData({ gpsOffsetX: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 0.00"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">gpsOffsetY</label>
                  <input
                    type="number"
                    step="0.01"
                    value={data.gpsOffsetY ?? ''}
                    onChange={e => updateFormData({ gpsOffsetY: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 0.00"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">gpsOffsetZ</label>
                  <input
                    type="number"
                    step="0.01"
                    value={data.gpsOffsetZ ?? ''}
                    onChange={e => updateFormData({ gpsOffsetZ: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 2.60"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Tipo de dirección (steeringType)</label>
                  <select
                    value={data.steeringType ?? 'ackermann'}
                    onChange={e => updateFormData({ steeringType: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
                  >
                    {STEERING_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Ejes directrices (steeringAxles)</label>
                  <input
                    type="text"
                    value={data.steeringAxles ?? ''}
                    onChange={e => updateFormData({ steeringAxles: e.target.value })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: front"
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <h4 className="text-sm font-medium text-gray-700">Dimensiones apero (m)</h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Ancho efectivo (implementWidth)</label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={data.implementWidth ?? ''}
                    onChange={e => updateFormData({ implementWidth: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 24.00"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Longitud apero (implementLength)</label>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={data.implementLength ?? ''}
                    onChange={e => updateFormData({ implementLength: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 3.20"
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Offset enganche X (hitchOffsetX)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={data.hitchOffsetX ?? ''}
                    onChange={e => updateFormData({ hitchOffsetX: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: -0.45"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 mb-1">Offset apero X (implementOffsetX)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={data.implementOffsetX ?? ''}
                    onChange={e => updateFormData({ implementOffsetX: e.target.value === '' ? undefined : Number(e.target.value) })}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500"
                    placeholder="Ej: 0.00"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-1">Tipo de enganche (hitchType)</label>
                <select
                  value={data.hitchType ?? ''}
                  onChange={e => updateFormData({ hitchType: e.target.value })}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 bg-white"
                >
                  <option value="">-- Selecciona tipo de enganche --</option>
                  {HITCH_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
