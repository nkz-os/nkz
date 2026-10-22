/// <reference types="vitest/globals" />
import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest';

// Cesium mock — registered before any module that imports 'cesium'
vi.mock('cesium', () => import('./cesium-mock'));

// Mock import.meta.env for Vitest
// In test environment, DEV is true by default
