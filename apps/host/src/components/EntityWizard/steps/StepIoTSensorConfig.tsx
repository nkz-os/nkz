import { useState, useEffect, useMemo } from 'react';
import { Cable, Activity, Zap, HelpCircle, Settings, Plus, Trash2 } from 'lucide-react';
import { useWizard } from '../WizardContext';
import { listDeviceProfiles, createDeviceProfile, type DeviceProfile } from '@/services/deviceProfilesApi';
import { DeviceProfileHelpModal } from '../../DeviceProfileHelpModal';
import type { IoTSensorFormData } from '../types';
import { useNotification } from '@/hooks/useNotification';
import { Button, Input } from '@nekazari/ui-kit';

/* eslint-disable @typescript-eslint/no-explicit-any */
export function StepIoTSensorConfig() {
  const { showNotification } = useNotification();
  const { entityType, formData, updateFormData } = useWizard();
  const [deviceProfiles, setDeviceProfiles] = useState<DeviceProfile[]>([]);
  const [showHelp, setShowHelp] = useState(false);
  const [newVariableName, setNewVariableName] = useState('');

  useEffect(() => {
    if (!entityType) return;
    listDeviceProfiles({ sdm_entity_type: entityType })
      .then(setDeviceProfiles)
      .catch(() => setDeviceProfiles([]));
  }, [entityType]);

  const formDataSafe = formData as IoTSensorFormData | null;

  // ── Health & Calibration helpers (hooks before early return per React rules) ──
  const selectedProfile = formDataSafe ? deviceProfiles.find(p => p.id === formDataSafe.deviceProfileId) : undefined;
  const profileVariables = useMemo(
    () => selectedProfile?.mappings?.map(m => m.target_attribute) ?? [],
    [selectedProfile]
  );

  const currentHealth = (formDataSafe as any)?.healthConfig ?? {};
  const currentCalibration = (formDataSafe as any)?.calibrationConfig ?? {};

  if (!formData || formData.macroCategory !== 'sensors') return null;
  const data = formData as IoTSensorFormData;
  const customVarKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const k of Object.keys(currentHealth)) {
      if (k !== 'communicationTimeoutHours') keys.add(k);
    }
    for (const k of Object.keys(currentCalibration)) {
      keys.add(k);
    }
    return keys;
  }, [currentHealth, currentCalibration]);
  const allVariables = useMemo(
    () => [...new Set([...profileVariables, ...customVarKeys])],
    [profileVariables, customVarKeys]
  );

  function updateHealthVar(variable: string, field: string, value: number | undefined) {
    const current = { ...((data as any).healthConfig ?? {}) };
    if (!current[variable]) current[variable] = {};
    current[variable] = { ...current[variable], [field]: value };
    (updateFormData as any)({ healthConfig: current });
  }

  function updateCalibrationVar(variable: string, field: string, value: any) {
    const current = { ...((data as any).calibrationConfig ?? {}) };
    if (!current[variable]) current[variable] = {};
    current[variable] = { ...current[variable], [field]: value };
    (updateFormData as any)({ calibrationConfig: current });
  }

  function updateCommunicationTimeout(value: number | undefined) {
    const current = { ...((data as any).healthConfig ?? {}) };
    current.communicationTimeoutHours = value;
    (updateFormData as any)({ healthConfig: current });
  }

  function addVariable() {
    const name = newVariableName.trim();
    if (!name || allVariables.includes(name)) return;
    // Initialise empty entries for the new variable
    const hc = { ...((data as any).healthConfig ?? {}) };
    hc[name] = hc[name] ?? {};
    (updateFormData as any)({ healthConfig: hc });

    const cc = { ...((data as any).calibrationConfig ?? {}) };
    cc[name] = cc[name] ?? { slope: 1.0, offset: 0.0, sensorHardwareId: '' };
    (updateFormData as any)({ calibrationConfig: cc });

    setNewVariableName('');
  }

  function removeVariable(variable: string) {
    const hc = { ...((data as any).healthConfig ?? {}) };
    delete hc[variable];
    (updateFormData as any)({ healthConfig: hc });

    const cc = { ...((data as any).calibrationConfig ?? {}) };
    delete cc[variable];
    (updateFormData as any)({ calibrationConfig: cc });
  }

  const handleImportProfile = (file: File) => {
    const reader = new FileReader();
    reader.onload = async event => {
      try {
        const json = JSON.parse(event.target?.result as string);
        if (json.name && json.sdm_entity_type && Array.isArray(json.mappings)) {
          await createDeviceProfile({
            name: json.name,
            description: json.description ?? '',
            sdm_entity_type: json.sdm_entity_type,
            mappings: json.mappings,
            is_public: false,
          });
          alert(`Perfil "${json.name}" importado. Selecciónalo de la lista.`);
          const updated = await listDeviceProfiles({ sdm_entity_type: entityType ?? undefined });
          setDeviceProfiles(updated);
        } else {
          showNotification({ type: 'error', message: 'El JSON debe contener: name, sdm_entity_type y mappings[]' });
        }
      } catch {
        showNotification({ type: 'error', message: 'Error al leer el archivo JSON. Verifica el formato.' });
      }
    };
    reader.readAsText(file);
  };

  const publicProfiles  = deviceProfiles.filter(p => p.is_public);
  const privateProfiles = deviceProfiles.filter(p => !p.is_public);

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">Datos del sensor</h3>

      {/* Name */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Nombre *</label>
        <Input
          type="text"
          value={data.name}
          onChange={(e: any) => updateFormData({ name: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-teal-500"
          placeholder="Ej: Sensor suelo parcela norte"
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">Descripción</label>
        <textarea
          value={data.description ?? ''}
          onChange={(e: any) => updateFormData({ description: e.target.value })}
          className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-teal-500"
          placeholder="Descripción opcional"
          rows={2}
        />
      </div>

      {/* Device profile */}
      <div className="pt-4 border-t bg-purple-50 p-4 rounded-xl border border-purple-100">
        <div className="flex items-center gap-2 mb-3">
          <div className="p-2 bg-purple-100 rounded-lg">
            <Cable className="w-5 h-5 text-purple-600" />
          </div>
          <div>
            <h4 className="text-sm font-bold text-gray-800">Conectividad IoT y Datos</h4>
            <p className="text-xs text-purple-700">Configura cómo este dispositivo enviará datos</p>
          </div>
        </div>

        <div className="space-y-4">
          {/* Profile selector */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Perfil de Dispositivo (Mapeo de Datos) *
            </label>
            <div className="flex gap-2">
              <select
                value={data.deviceProfileId ?? ''}
                onChange={(e: any) => updateFormData({ deviceProfileId: e.target.value || null })}
                className={`flex-1 px-4 py-2 border rounded-lg focus:ring-2 focus:ring-purple-500 bg-white ${
                  !data.deviceProfileId ? 'border-red-300' : 'border-nkz-border'
                }`}
              >
                <option value="">-- Selecciona un perfil --</option>
                {publicProfiles.length > 0 && (
                  <optgroup label="🏛️ Perfiles Oficiales">
                    {publicProfiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </optgroup>
                )}
                {privateProfiles.length > 0 && (
                  <optgroup label="🏠 Mis Perfiles">
                    {privateProfiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </optgroup>
                )}
              </select>
              <Button
                onClick={() => setShowHelp(true)}
                className="px-3 py-2 bg-white border border-nkz-border rounded-lg hover:bg-nkz-bg-secondary text-gray-600 flex items-center"
                title="Ayuda y plantillas"
              >
                <HelpCircle className="w-5 h-5" />
              </Button>
            </div>
          </div>

          {/* Actions row */}
          <div className="grid grid-cols-3 gap-3">
            <Button
              type="button"
              onClick={() => setShowHelp(true)}
              className="flex items-center justify-center gap-2 text-xs font-medium text-purple-700 bg-purple-100 hover:bg-purple-200 py-2 rounded-lg border border-purple-200"
            >
              <Activity className="w-3 h-3" /> Ver Plantillas
            </Button>

            <label className="flex items-center justify-center gap-2 text-xs font-medium text-nkz-info bg-nkz-info-light hover:bg-blue-200 py-2 rounded-lg border border-blue-200 cursor-pointer">
              <Input
                type="file"
                accept=".json,application/json"
                className="hidden"
                onChange={(e: any) => {
                  const file = e.target.files?.[0];
                  if (file) handleImportProfile(file);
                  e.target.value = '';
                }}
              />
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
              </svg>
              Importar JSON
            </label>

            <div className="flex items-center justify-center gap-2 text-xs text-nkz-muted bg-white border border-nkz-border py-2 rounded-lg">
              <Zap className="w-3 h-3 text-yellow-500" />
              Credenciales MQTT al finalizar
            </div>
          </div>

          <p className="text-xs text-nkz-muted italic">
            * El perfil de dispositivo es obligatorio. Define cómo se traducen los datos del datalogger a atributos SDM estándar. Si no encuentras un perfil adecuado, importa uno o crea uno nuevo desde "Ver Plantillas".
          </p>
        </div>
      </div>

      {/* ── Health Rules & Calibration Accordion ── */}
      <details className="group mt-6 border border-nkz-border rounded-xl overflow-hidden">
        <summary className="flex items-center gap-2 px-4 py-3 bg-gray-50 hover:bg-gray-100 cursor-pointer list-none">
          <Settings className="w-5 h-5 text-teal-600" />
          <span className="text-sm font-semibold text-gray-800">
            Configuración de Fiabilidad y Calibración (Health Rules)
          </span>
          <span className="ml-auto text-xs text-nkz-muted transition-transform group-open:rotate-180">▼</span>
        </summary>

        <div className="p-4 space-y-4 border-t border-nkz-border">
          <p className="text-xs text-nkz-muted">
            Define reglas de validación y calibración para las variables del sensor.
            Estos valores se usan para detectar lecturas anómalas y transformar señales en bruto.
          </p>

          {/* Communication timeout */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Communication Timeout (horas)
            </label>
            <Input
              type="number"
              min="0"
              step="0.5"
              value={(currentHealth as any).communicationTimeoutHours ?? ''}
              onChange={(e: any) => updateCommunicationTimeout(e.target.value !== '' ? Number(e.target.value) : undefined)}
              className="w-full px-4 py-2 border border-nkz-border rounded-lg focus:ring-2 focus:ring-teal-500"
              placeholder="Ej: 24"
            />
            <p className="text-xs text-nkz-muted mt-1">
              Horas sin datos antes de marcar el sensor como no disponible.
            </p>
          </div>

          {/* Variable */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-semibold text-gray-800">
                Variables ({allVariables.length})
              </h4>
              <div className="flex gap-2">
                <Input
                  type="text"
                  value={newVariableName}
                  onChange={(e: any) => setNewVariableName(e.target.value)}
                  className="px-3 py-1.5 text-sm border border-nkz-border rounded-lg w-40"
                  placeholder="Nueva variable..."
                  onKeyDown={(e: any) => { if (e.key === 'Enter') { e.preventDefault(); addVariable(); } }}
                />
                <Button
                  onClick={addVariable}
                  disabled={!newVariableName.trim()}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-teal-700 bg-teal-50 hover:bg-teal-100 border border-teal-200 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Plus className="w-3 h-3" /> Añadir
                </Button>
              </div>
            </div>

            {allVariables.length === 0 && (
              <p className="text-xs text-nkz-muted italic py-2">
                Selecciona un perfil de dispositivo o añade variables manualmente para configurar reglas de salud y calibración.
              </p>
            )}

            {allVariables.map(variable => {
              const vHealth = (currentHealth as any)[variable] ?? {};
              const vCalib = (currentCalibration as any)[variable] ?? {};
              return (
                <div key={variable} className="border border-nkz-border rounded-lg p-3 bg-white">
                  <div className="flex items-center justify-between mb-2">
                    <h5 className="text-sm font-semibold text-gray-800 font-mono">{variable}</h5>
                    <button
                      type="button"
                      onClick={() => removeVariable(variable)}
                      className="text-red-400 hover:text-red-600 p-1"
                      title="Eliminar variable"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                    {/* Health: minValid */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-0.5">Min Valid</label>
                      <Input
                        type="number"
                        value={vHealth.minValid ?? ''}
                        onChange={(e: any) => updateHealthVar(variable, 'minValid', e.target.value !== '' ? Number(e.target.value) : undefined)}
                        className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
                        placeholder="—"
                      />
                    </div>
                    {/* Health: maxValid */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-0.5">Max Valid</label>
                      <Input
                        type="number"
                        value={vHealth.maxValid ?? ''}
                        onChange={(e: any) => updateHealthVar(variable, 'maxValid', e.target.value !== '' ? Number(e.target.value) : undefined)}
                        className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
                        placeholder="—"
                      />
                    </div>
                    {/* Health: maxStagnantHours */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-0.5">Max Stagnant (h)</label>
                      <Input
                        type="number"
                        min="0"
                        step="0.5"
                        value={vHealth.maxStagnantHours ?? ''}
                        onChange={(e: any) => updateHealthVar(variable, 'maxStagnantHours', e.target.value !== '' ? Number(e.target.value) : undefined)}
                        className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
                        placeholder="—"
                      />
                    </div>
                    {/* Calibration: slope */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-0.5">Slope</label>
                      <Input
                        type="number"
                        step="0.01"
                        value={vCalib.slope ?? 1.0}
                        onChange={(e: any) => updateCalibrationVar(variable, 'slope', e.target.value !== '' ? Number(e.target.value) : 1.0)}
                        className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
                      />
                    </div>
                    {/* Calibration: offset */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-0.5">Offset</label>
                      <Input
                        type="number"
                        step="0.01"
                        value={vCalib.offset ?? 0.0}
                        onChange={(e: any) => updateCalibrationVar(variable, 'offset', e.target.value !== '' ? Number(e.target.value) : 0.0)}
                        className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
                      />
                    </div>
                    {/* Calibration: sensorHardwareId */}
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-0.5">Hardware ID</label>
                      <Input
                        type="text"
                        value={vCalib.sensorHardwareId ?? ''}
                        onChange={(e: any) => updateCalibrationVar(variable, 'sensorHardwareId', e.target.value)}
                        className="w-full px-3 py-1.5 text-sm border border-nkz-border rounded-lg"
                        placeholder="SN-..."
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </details>

      <DeviceProfileHelpModal isOpen={showHelp} onClose={() => setShowHelp(false)} />
    </div>
  );
}
