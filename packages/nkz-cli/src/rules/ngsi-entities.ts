/** Known FIWARE Smart Data Model entity types. Based on PLATFORM_CONVENTIONS.md Section 6. */
export const KNOWN_ENTITY_TYPES: Set<string> = new Set([
  'AgriParcel', 'AgriSensor', 'AgriCrop', 'AgriSoil', 'AgriFarm',
  'AgriGreenhouse', 'AgriParcelRecord', 'AgriParcelOperation',
  'AgriculturalTractor', 'AgriculturalImplement', 'AgriculturalRobot',
  'WaterSource', 'Well', 'Spring', 'Pond', 'IrrigationOutlet', 'IrrigationSystem',
  'Building', 'FarmBuilding', 'Silo', 'Greenhouse',
  'PhotovoltaicInstallation', 'EnergyStorageSystem', 'WindTurbine',
  'LivestockAnimal', 'LivestockGroup', 'LivestockFarm', 'LivestockBarn',
  'WeatherObserved', 'WeatherForecast', 'WeatherAlert',
  'Device', 'DeviceModel', 'Fleet', 'Property',
  'Vehicle', 'VehicleFleet', 'Road', 'RoadSegment',
  'AirQualityObserved', 'NoiseLevelObserved', 'WaterQualityObserved',
]);
