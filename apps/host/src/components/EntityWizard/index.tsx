/**
 * EntityWizard 3.0 — modular per-category wizard
 *
 * Architecture:
 *   - WizardProvider holds all form state (typed discriminated union)
 *   - placementState lives here as local useReducer (UI state, not form payload)
 *   - Steps are routed by macroCategory; step count varies per flow
 *   - Submission is delegated to pure handlers in submission/
 */

import { useReducer, useState, useEffect, useCallback } from 'react';
import {
  X, ArrowRight, ArrowLeft, Check, Loader2,
  Search, Settings, Radio, Tractor, MapPin, Palette, FileText,
} from 'lucide-react';
import { useViewer } from '@/context/ViewerContext';
import { placementReducer, INITIAL_STATE } from '@/machines/placementMachine';
import { WizardProvider, useWizard } from './WizardContext';
import { StepTypeSelection } from './steps/StepTypeSelection';
import { StepGeoAssetConfig } from './steps/StepGeoAssetConfig';
import { StepIoTSensorConfig } from './steps/StepIoTSensorConfig';
import { StepFleetConfig } from './steps/StepFleetConfig';
import { StepGeometry } from './steps/StepGeometry';
import { StepVisualization } from './steps/StepVisualization';
import { StepSummary } from './steps/StepSummary';
import { RobotCredentialsModal, type RobotCredentials } from './RobotCredentialsModal';
import { MqttCredentialsModal, type MqttCredentials } from './MqttCredentialsModal';
import { submitGeoAsset } from './submission/submitGeoAsset';
import { submitIoTSensor } from './submission/submitIoTSensor';
import { submitFleet } from './submission/submitFleet';
import type { EntityWizardProps, GeoAssetFormData, IoTSensorFormData, FleetFormData } from './types';
import type { StepId } from './types';
import { Button } from '@nekazari/ui-kit';

// ─── Step icon map ───────────────────────────────────────────────────────────

const STEP_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  type: Search,
  'geo-config': Settings,
  'iot-config': Radio,
  'fleet-config': Tractor,
  geometry: MapPin,
  visualization: Palette,
  summary: FileText,
};

// ─── Step router ──────────────────────────────────────────────────────────────

/* eslint-disable @typescript-eslint/no-explicit-any */
interface StepRouterProps {
  stepId: StepId;
  placementState: ReturnType<typeof placementReducer>;
  dispatchPlacement: React.Dispatch<Parameters<typeof placementReducer>[1]>;
}

function StepRouter({ stepId, placementState, dispatchPlacement }: StepRouterProps) {
  switch (stepId) {
    case 'type':        return <StepTypeSelection />;
    case 'geo-config':  return <StepGeoAssetConfig />;
    case 'iot-config':  return <StepIoTSensorConfig />;
    case 'fleet-config': return <StepFleetConfig />;
    case 'geometry':    return <StepGeometry placementState={placementState} dispatchPlacement={dispatchPlacement} />;
    case 'visualization': return <StepVisualization />;
    case 'summary':     return <StepSummary />;
    default:            return null;
  }
}

// ─── Stepper indicator with icons ────────────────────────────────────────────

function StepperIndicator() {
  const { steps, stepIndex } = useWizard();
  return (
    <div className="flex items-center gap-0 mt-2">
      {steps.map((s, i) => {
        const Icon = STEP_ICONS[s.id] ?? Search;
        const isActive = i === stepIndex;
        const isDone = i < stepIndex;
        return (
          <div key={s.id} className="flex items-center">
            {/* Step dot + icon */}
            <div className="flex flex-col items-center">
              <div
                className={`flex items-center justify-center w-8 h-8 rounded-full transition-all duration-300 ${
                  isDone
                    ? 'bg-nkz-accent-base text-white shadow-sm'
                    : isActive
                      ? 'bg-nkz-accent-soft text-nkz-accent-strong ring-2 ring-nkz-accent-base/30'
                      : 'bg-gray-100 text-gray-400'
                }`}
              >
                {isDone ? (
                  <Check className="w-4 h-4" />
                ) : (
                  <Icon className="w-4 h-4" />
                )}
              </div>
              <span
                className={`text-[10px] mt-1 whitespace-nowrap transition-colors ${
                  isActive
                    ? 'font-semibold text-nkz-accent-strong'
                    : isDone
                      ? 'text-nkz-text-secondary'
                      : 'text-gray-400'
                }`}
              >
                {s.label}
              </span>
            </div>
            {/* Connector line */}
            {i < steps.length - 1 && (
              <div
                className={`w-8 h-px mx-1 self-start mt-4 transition-colors ${
                  i < stepIndex ? 'bg-nkz-accent-base' : 'bg-gray-200'
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Validate current step before advancing ───────────────────────────────────

function validateStep(
  stepId: StepId,
  entityType: string | null,
  formData: GeoAssetFormData | IoTSensorFormData | FleetFormData | null,
  placementState: ReturnType<typeof placementReducer>,
): string | null {
  switch (stepId) {
    case 'type':
      return entityType ? null : 'Por favor selecciona un tipo de entidad';

    case 'geo-config':
    case 'fleet-config':
      return formData?.name.trim() ? null : 'El nombre es obligatorio';

    case 'iot-config': {
      if (!formData?.name.trim()) return 'El nombre es obligatorio';
      const iotData = formData as IoTSensorFormData;
      if (!iotData.deviceProfileId) return 'El perfil de dispositivo es obligatorio para sensores IoT';
      return null;
    }

    case 'geometry':
      if (placementState.mode === 'stamp') {
        return placementState.stampedInstances.length > 0 ? null : 'Pinta al menos una instancia';
      }
      if (placementState.mode === 'array') {
        return placementState.stampedInstances.length > 0 ? null : 'Configura el punto de ancla y los parámetros de la grilla';
      }
      // Point geometry is optional (coordinates may be unknown)
      return null;

    default:
      return null;
  }
}

// ─── Inner wizard (inside WizardProvider) ─────────────────────────────────────

interface InnerWizardProps {
  onClose: () => void;
  onSuccess?: () => void;
}

function InnerWizard({ onClose, onSuccess }: InnerWizardProps) {
  const {
    entityType, formData, currentStep,
    isFirstStep, isLastStep, goNext, goBack,
    loading, error, validationError,
    setLoading, setError, reset,
  } = useWizard();

  const { mapMode } = useViewer();
  const [placementState, dispatchPlacement] = useReducer(placementReducer, INITIAL_STATE);
  const [robotCredentials, setRobotCredentials] = useState<RobotCredentials | null>(null);
  const [mqttCredentials, setMqttCredentials] = useState<MqttCredentials | null>(null);

  // Side panel mode: when interacting with the map (picking, drawing, stamping)
  // OR when on geometry step with array/stamp placement (always needs map access)
  const isGeometryWithPlacement = currentStep.id === 'geometry'
    && (placementState.mode === 'array' || placementState.mode === 'stamp');
  const isMapInteractMode = isGeometryWithPlacement
    || (mapMode as string) === 'STAMP_INSTANCES'
    || (mapMode as string) === 'PREVIEW_MODEL'
    || (mapMode as string) === 'DRAW_GEOMETRY'
    || (mapMode as string) === 'PICK_LOCATION';

  // Reset placement state when wizard resets
  useEffect(() => {
    dispatchPlacement({ type: 'RESET' });
  }, []);

  // useCallback MUST be called before any conditional return (React hooks rule)
  const handleClose = useCallback(() => {
    reset();
    dispatchPlacement({ type: 'RESET' });
    onClose();
  }, [reset, onClose]);

  const handleNext = () => {
    const err = validateStep(currentStep.id, entityType, formData as any, placementState);
    if (err) { setError(err); return; }
    goNext();
  };

  const handleSubmit = async () => {
    setLoading(true);
    setError(null);
    try {
      if (!entityType || !formData) throw new Error('Estado del wizard incompleto');

      switch (formData.macroCategory) {
        case 'assets': {
          await submitGeoAsset(entityType, formData as GeoAssetFormData, placementState);
          if (onSuccess) onSuccess();
          onClose();
          break;
        }
        case 'sensors': {
          const result = await submitIoTSensor(entityType, formData as IoTSensorFormData);
          if (result.mqttCredentials) {
            setMqttCredentials(result.mqttCredentials);
            // Don't close — wait for user to save credentials
          } else {
            if (onSuccess) onSuccess();
            onClose();
          }
          break;
        }
        case 'fleet': {
          const result = await submitFleet(entityType, formData as FleetFormData);
          if (result.robotCredentials) {
            setRobotCredentials(result.robotCredentials);
            if (onSuccess) onSuccess();
            // Don't close — wait for user to save credentials
          } else {
            if (onSuccess) onSuccess();
            onClose();
          }
          break;
        }
      }
    } catch (err: any) {
      const msg = err.response?.data?.error || err.message || 'Error al crear la entidad';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className={`fixed inset-0 z-50 flex items-start justify-center pt-10 ${
        isMapInteractMode
          ? 'pointer-events-none'
          : 'bg-black bg-opacity-50'
      }`}>
        <div className={`bg-white shadow-xl flex flex-col transition-all duration-300 ${
          isMapInteractMode
            ? 'absolute top-20 right-4 w-96 max-h-[80vh] pointer-events-auto rounded-xl border border-nkz-border'
            : 'rounded-2xl max-w-5xl w-full max-h-[85vh] mx-4'
        }`}>
          {/* Header */}
          <div className="bg-white px-8 pt-6 pb-4 border-b flex justify-between items-start sticky top-0 z-10 rounded-t-2xl">
            <div>
              <h2 className="text-xl font-bold text-nkz-text-primary">Crear Nueva Entidad</h2>
              <StepperIndicator />
            </div>
            <Button
              onClick={handleClose}
              variant="ghost"
              size="sm"
              className="text-nkz-text-muted hover:text-nkz-text-primary -mt-1"
            >
              <X className="w-5 h-5" />
            </Button>
          </div>

          {/* Content */}
          <div className="p-8 flex-1 overflow-y-auto">
            {(error || validationError) && (
              <div className="mb-6 bg-nkz-danger-soft border border-nkz-danger/20 text-nkz-danger-strong px-4 py-3 rounded-lg text-sm">
                {error ?? validationError}
              </div>
            )}
            <StepRouter
              stepId={currentStep.id}
              placementState={placementState}
              dispatchPlacement={dispatchPlacement}
            />
          </div>

          {/* Footer */}
          <div className="bg-nkz-bg-secondary px-8 py-4 border-t flex justify-between items-center rounded-b-2xl">
            <Button
              variant="ghost"
              size="md"
              onClick={goBack}
              disabled={isFirstStep || loading}
              leadingIcon={<ArrowLeft className="w-4 h-4" />}
            >
              Atrás
            </Button>

            {isLastStep ? (
              <Button
                variant="primary"
                size="md"
                onClick={handleSubmit}
                disabled={loading}
                trailingIcon={loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Check className="w-4 h-4" />}
              >
                {loading ? 'Creando...' : 'Crear Entidad'}
              </Button>
            ) : (
              <Button
                variant="primary"
                size="md"
                onClick={handleNext}
                disabled={loading}
                trailingIcon={<ArrowRight className="w-4 h-4" />}
              >
                Siguiente
              </Button>
            )}
          </div>
        </div>
      </div>

      {/* Loading overlay */}
      {loading && (
        <div className="fixed inset-0 z-[100] bg-black/60 flex items-center justify-center">
          <div className="bg-white p-8 rounded-3xl shadow-2xl flex flex-col items-center max-w-sm w-full mx-4">
            <Loader2 className="w-12 h-12 text-nkz-info animate-spin mb-4" />
            <h3 className="text-xl font-bold text-nkz-text-primary">Creando entidad...</h3>
            <p className="text-nkz-text-muted mt-2 text-sm text-center">No cierre esta ventana, por favor.</p>
          </div>
        </div>
      )}

      {/* Robot credentials */}
      {robotCredentials && (
        <RobotCredentialsModal
          isOpen
          onClose={() => { setRobotCredentials(null); onClose(); }}
          robotName={formData?.name ?? ''}
          credentials={robotCredentials}
        />
      )}

      {/* MQTT credentials */}
      {mqttCredentials && (
        <MqttCredentialsModal
          isOpen
          onClose={() => {
            setMqttCredentials(null);
            if (onSuccess) onSuccess();
            onClose();
          }}
          deviceName={formData?.name ?? ''}
          credentials={mqttCredentials}
        />
      )}
    </>
  );
}

// ─── Public export — wraps InnerWizard with the provider ─────────────────────

export const EntityWizard: React.FC<EntityWizardProps> = ({
  isOpen,
  onClose,
  onSuccess,
  initialEntityType,
}) => {
  if (!isOpen) return null;
  return (
    <WizardProvider initialEntityType={initialEntityType}>
      <InnerWizard onClose={onClose} onSuccess={onSuccess} />
    </WizardProvider>
  );
};
