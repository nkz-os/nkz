import type { AttributeSchema } from './types';

const MTR = 'MTR';

export const KNOWN_SCHEMAS: AttributeSchema[] = [
  // ── Basic (all types) ──
  { key: 'name', labelKey: 'editor.field.name', type: 'text', section: 'basic', entityTypes: ['*'] },
  { key: 'description', labelKey: 'editor.field.description', type: 'text', section: 'basic', entityTypes: ['*'] },
  { key: 'category', labelKey: 'editor.field.category', type: 'text', section: 'basic', entityTypes: ['ManufacturingMachine'] },
  { key: 'manufacturer', labelKey: 'editor.field.manufacturer', type: 'text', section: 'basic', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine'] },
  { key: 'serialNumber', labelKey: 'editor.field.serialNumber', type: 'text', section: 'basic', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine'] },
  { key: 'isobusCompatible', labelKey: 'editor.field.isobusCompatible', type: 'boolean', section: 'basic', entityTypes: ['ManufacturingMachine'] },
  { key: 'status', labelKey: 'editor.field.status', type: 'text', section: 'basic', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'Device'] },
  { key: 'notes', labelKey: 'editor.field.notes', type: 'text', section: 'basic', entityTypes: ['*'] },
  { key: 'elevation', labelKey: 'editor.field.elevation', type: 'number', section: 'basic', unitCode: MTR, entityTypes: ['AgriParcel', 'Vineyard', 'OliveGrove'] },

  // ── Geometry ──
  { key: 'location', labelKey: 'editor.field.location', type: 'geo', section: 'geometry', entityTypes: ['*'] },

  // ── Kinematic (ManufacturingMachine) ──
  { key: 'trackWidth', labelKey: 'editor.field.trackWidth', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'wheelbase', labelKey: 'editor.field.wheelbase', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'minTurningRadius', labelKey: 'editor.field.minTurningRadius', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'gpsOffsetX', labelKey: 'editor.field.gpsOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'gpsOffsetY', labelKey: 'editor.field.gpsOffsetY', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'gpsOffsetZ', labelKey: 'editor.field.gpsOffsetZ', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'hitchType', labelKey: 'editor.field.hitchType', type: 'select', section: 'kinematic',
    options: [
      { value: 'none', labelKey: 'editor.hitch.none' },
      { value: 'three_point', labelKey: 'editor.hitch.three_point' },
      { value: 'drawbar', labelKey: 'editor.hitch.drawbar' },
      { value: 'pintle', labelKey: 'editor.hitch.pintle' },
      { value: 'ball', labelKey: 'editor.hitch.ball' },
    ], entityTypes: ['ManufacturingMachine'] },
  { key: 'hitchOffsetX', labelKey: 'editor.field.hitchOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'implementLength', labelKey: 'editor.field.implementLength', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'implementWidth', labelKey: 'editor.field.implementWidth', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'implementOffsetX', labelKey: 'editor.field.implementOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: ['ManufacturingMachine'] },
  { key: 'steeringType', labelKey: 'editor.field.steeringType', type: 'select', section: 'kinematic',
    options: [
      { value: 'ackermann', labelKey: 'editor.steering.ackermann' },
      { value: 'articulated', labelKey: 'editor.steering.articulated' },
      { value: 'skid_steer', labelKey: 'editor.steering.skid_steer' },
      { value: 'differential', labelKey: 'editor.steering.differential' },
    ], entityTypes: ['ManufacturingMachine'] },
  { key: 'steeringAxles', labelKey: 'editor.field.steeringAxles', type: 'select', section: 'kinematic',
    options: [
      { value: 'front', labelKey: 'editor.axle.front' },
      { value: 'rear', labelKey: 'editor.axle.rear' },
      { value: 'both', labelKey: 'editor.axle.both' },
      { value: 'none', labelKey: 'editor.axle.none' },
    ], entityTypes: ['ManufacturingMachine'] },

  // ── Relationships ──
  { key: 'refAgriParcel', labelKey: 'editor.field.refAgriParcel', type: 'relationship', section: 'relationships', targetType: 'AgriParcel', entityTypes: ['AgriSensor', 'Device', 'WeatherObserved', 'AgriEnergyTracker', 'PhotovoltaicInstallation', 'EnergyStorageSystem'] },
  // ASSUMPTION: refAgriFarm is the canonical NGSI-LD name. Legacy parcelApi.ts uses refFarm — this is a known bug filed separately.
  { key: 'refAgriFarm', labelKey: 'editor.field.refAgriFarm', type: 'relationship', section: 'relationships', targetType: 'AgriFarm', entityTypes: ['AgriParcel', 'Vineyard', 'OliveGrove', 'AutonomousMobileRobot', 'ManufacturingMachine'] },
  { key: 'refDeviceProfile', labelKey: 'editor.field.refDeviceProfile', type: 'relationship', section: 'relationships', targetType: 'DeviceProfile', entityTypes: ['AgriSensor', 'Device'] },
  { key: 'refParent', labelKey: 'editor.field.refParent', type: 'relationship', section: 'relationships', entityTypes: ['AgriParcel', 'Vineyard', 'OliveGrove', 'AgriBuilding'] },

  // ── Visual ──
  { key: 'icon2d', labelKey: 'editor.field.icon2d', type: 'text', section: 'visual', entityTypes: ['*'] },
  { key: 'ref3DModel', labelKey: 'editor.field.ref3DModel', type: 'text', section: 'visual', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'AgriBuilding'] },
  { key: 'modelScale', labelKey: 'editor.field.modelScale', type: 'number', section: 'visual', step: 0.1, entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'AgriBuilding'] },
  { key: 'modelRotation', labelKey: 'editor.field.modelRotation', type: 'number', section: 'visual', step: 1, entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'AgriBuilding'] },
];

export function getSchemasForType(entityType: string): AttributeSchema[] {
  return KNOWN_SCHEMAS.filter(s =>
    s.entityTypes.includes('*') || s.entityTypes.includes(entityType)
  );
}

export function getSchemaByKey(key: string, entityType: string): AttributeSchema | undefined {
  return KNOWN_SCHEMAS.find(s => s.key === key &&
    (s.entityTypes.includes('*') || s.entityTypes.includes(entityType)));
}

export function hasSection(section: AttributeSchema['section'], entityType: string): boolean {
  return KNOWN_SCHEMAS.some(s => s.section === section &&
    (s.entityTypes.includes('*') || s.entityTypes.includes(entityType)));
}
