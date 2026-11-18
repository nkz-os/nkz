import type { AttributeSchema } from './types';

const MTR = 'MTR';
const MM = ['ManufacturingMachine'];
const TRACTOR = ['tractor'];
const IMPLEMENT = ['implement'];
/** Roles a ManufacturingMachine `category` can take; any other value shows every geometry field. */
export const MACHINE_CATEGORIES = ['tractor', 'implement'];

export const KNOWN_SCHEMAS: AttributeSchema[] = [
  // ── Basic (all types) ──
  { key: 'name', labelKey: 'editor.field.name', type: 'text', section: 'basic', entityTypes: ['*'] },
  { key: 'description', labelKey: 'editor.field.description', type: 'text', section: 'basic', entityTypes: ['*'] },
  { key: 'category', labelKey: 'editor.field.category', type: 'select', section: 'basic', entityTypes: ['ManufacturingMachine'],
    options: [
      { value: 'tractor', labelKey: 'editor.category.tractor' },
      { value: 'implement', labelKey: 'editor.category.implement' },
    ] },
  { key: 'manufacturer', labelKey: 'editor.field.manufacturer', type: 'text', section: 'basic', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine'] },
  { key: 'serialNumber', labelKey: 'editor.field.serialNumber', type: 'text', section: 'basic', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine'] },
  { key: 'isobusCompatible', labelKey: 'editor.field.isobusCompatible', type: 'boolean', section: 'basic', entityTypes: ['ManufacturingMachine'] },
  { key: 'status', labelKey: 'editor.field.status', type: 'text', section: 'basic', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'Device'] },
  { key: 'notes', labelKey: 'editor.field.notes', type: 'text', section: 'basic', entityTypes: ['*'] },
  { key: 'elevation', labelKey: 'editor.field.elevation', type: 'number', section: 'basic', unitCode: MTR, entityTypes: ['AgriParcel', 'Vineyard', 'OliveGrove'] },

  // ── Geometry ──
  { key: 'location', labelKey: 'editor.field.location', type: 'geo', section: 'geometry', entityTypes: ['*'] },

  // ── Kinematic (ManufacturingMachine) ──
  // Frame (ISO 11783-10): X forward, Y right, Z up, metres. Tractor origin: rear-axle centre at ground level
  // (machine centre for articulated/tracked/differential steering). Implement origin: its hitch point.
  // `categories` shows each field for the machine role it describes; a machine without a known category sees all.
  { key: 'minTurningRadius', labelKey: 'editor.field.minTurningRadius', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.minTurningRadius' },
  { key: 'trackWidth', labelKey: 'editor.field.trackWidth', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.trackWidth' },
  { key: 'wheelbase', labelKey: 'editor.field.wheelbase', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.wheelbase' },
  { key: 'steeringType', labelKey: 'editor.field.steeringType', type: 'select', section: 'kinematic',
    options: [
      { value: 'ackermann', labelKey: 'editor.steering.ackermann' },
      { value: 'articulated', labelKey: 'editor.steering.articulated' },
      { value: 'skid_steer', labelKey: 'editor.steering.skid_steer' },
      { value: 'differential', labelKey: 'editor.steering.differential' },
    ], entityTypes: MM, categories: TRACTOR },
  { key: 'steeringAxles', labelKey: 'editor.field.steeringAxles', type: 'select', section: 'kinematic',
    options: [
      { value: 'front', labelKey: 'editor.axle.front' },
      { value: 'rear', labelKey: 'editor.axle.rear' },
      { value: 'both', labelKey: 'editor.axle.both' },
      { value: 'none', labelKey: 'editor.axle.none' },
    ], entityTypes: MM, categories: TRACTOR },
  { key: 'gpsOffsetX', labelKey: 'editor.field.gpsOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.gpsOffsetX' },
  { key: 'gpsOffsetY', labelKey: 'editor.field.gpsOffsetY', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.gpsOffsetY' },
  { key: 'gpsOffsetZ', labelKey: 'editor.field.gpsOffsetZ', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.gpsOffsetZ' },
  { key: 'hitchOffsetX', labelKey: 'editor.field.hitchOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.hitchOffsetX' },
  { key: 'frontHitchOffsetX', labelKey: 'editor.field.frontHitchOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.frontHitchOffsetX' },
  { key: 'guidanceDeviceOffsetX', labelKey: 'editor.field.guidanceDeviceOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.guidanceDeviceOffsetX' },
  { key: 'guidanceDeviceOffsetY', labelKey: 'editor.field.guidanceDeviceOffsetY', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, categories: TRACTOR, helpKey: 'editor.help.guidanceDeviceOffsetY' },
  { key: 'hitchType', labelKey: 'editor.field.hitchType', type: 'select', section: 'kinematic',
    options: [
      { value: 'none', labelKey: 'editor.hitch.none' },
      { value: 'three_point', labelKey: 'editor.hitch.three_point' },
      { value: 'drawbar', labelKey: 'editor.hitch.drawbar' },
      { value: 'pintle', labelKey: 'editor.hitch.pintle' },
      { value: 'ball', labelKey: 'editor.hitch.ball' },
    ], entityTypes: MM, categories: IMPLEMENT },
  { key: 'mountPosition', labelKey: 'editor.field.mountPosition', type: 'select', section: 'kinematic',
    options: [
      { value: 'rear', labelKey: 'editor.mount.rear' },
      { value: 'front', labelKey: 'editor.mount.front' },
    ], entityTypes: MM, categories: IMPLEMENT, helpKey: 'editor.help.mountPosition' },
  // Working fields: on the implement, or on a self-propelled machine (category tractor) used alone.
  { key: 'implementWidth', labelKey: 'editor.field.implementWidth', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: MM, helpKey: 'editor.help.implementWidth' },
  { key: 'transportWidth', labelKey: 'editor.field.transportWidth', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: MM, categories: IMPLEMENT, helpKey: 'editor.help.transportWidth' },
  { key: 'implementOffsetX', labelKey: 'editor.field.implementOffsetX', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, helpKey: 'editor.help.implementOffsetX' },
  { key: 'implementOffsetY', labelKey: 'editor.field.implementOffsetY', type: 'number', section: 'kinematic', unitCode: MTR, step: 0.01, entityTypes: MM, helpKey: 'editor.help.implementOffsetY' },
  { key: 'implementLength', labelKey: 'editor.field.implementLength', type: 'number', section: 'kinematic', unitCode: MTR, min: 0, step: 0.01, entityTypes: MM, categories: IMPLEMENT, helpKey: 'editor.help.implementLength' },

  // ── Relationships ──
  { key: 'refAgriParcel', labelKey: 'editor.field.refAgriParcel', type: 'relationship', section: 'relationships', targetType: 'AgriParcel', entityTypes: ['AgriSensor', 'Device', 'WeatherObserved', 'AgriEnergyTracker', 'PhotovoltaicInstallation', 'EnergyStorageSystem'] },
  // ASSUMPTION: refAgriFarm is the canonical NGSI-LD name. Legacy parcelApi.ts uses refFarm — this is a known bug filed separately.
  { key: 'refAgriFarm', labelKey: 'editor.field.refAgriFarm', type: 'relationship', section: 'relationships', targetType: 'AgriFarm', entityTypes: ['AgriParcel', 'Vineyard', 'OliveGrove', 'AutonomousMobileRobot', 'ManufacturingMachine'] },
  { key: 'refDeviceProfile', labelKey: 'editor.field.refDeviceProfile', type: 'relationship', section: 'relationships', targetType: 'DeviceProfile', entityTypes: ['AgriSensor', 'Device'] },
  // A subparcel's link to its parent is hasAgriParcel, which the broker returns under its alias.
  { key: 'refAgriParcel', labelKey: 'editor.field.refParent', type: 'relationship', section: 'relationships', targetType: 'AgriParcel', entityTypes: ['AgriParcel'] },
  { key: 'refParent', labelKey: 'editor.field.refParent', type: 'relationship', section: 'relationships', entityTypes: ['Vineyard', 'OliveGrove', 'AgriBuilding'] },

  // ── Visual ──
  { key: 'icon2d', labelKey: 'editor.field.icon2d', type: 'text', section: 'visual', entityTypes: ['*'] },
  { key: 'ref3DModel', labelKey: 'editor.field.ref3DModel', type: 'text', section: 'visual', entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'AgriBuilding'] },
  { key: 'modelScale', labelKey: 'editor.field.modelScale', type: 'number', section: 'visual', step: 0.1, entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'AgriBuilding'] },
  { key: 'modelRotation', labelKey: 'editor.field.modelRotation', type: 'number', section: 'visual', step: 1, entityTypes: ['AutonomousMobileRobot', 'ManufacturingMachine', 'AgriBuilding'] },
];

export function getSchemasForType(entityType: string, category?: string): AttributeSchema[] {
  const known = category !== undefined && MACHINE_CATEGORIES.includes(category);
  return KNOWN_SCHEMAS.filter(s =>
    (s.entityTypes.includes('*') || s.entityTypes.includes(entityType)) &&
    (!known || !s.categories || s.categories.includes(category as string))
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
