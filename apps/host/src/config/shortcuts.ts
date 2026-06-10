// Canonical keyboard shortcuts for the Unified Viewer.
// Registered on mount by the host. Modules should NOT register global listeners
// for keys in this map — use useKeyboardShortcut from viewer-kit to avoid collisions.

export interface ViewerShortcut {
  key: string;
  ctrl?: boolean;
  shift?: boolean;
  description: string;
}

export const VIEWER_SHORTCUTS: ViewerShortcut[] = [
  // Camera
  { key: 'r', description: 'Reset camera north' },
  { key: '2', description: '2D view' },
  { key: '3', description: '3D view' },

  // Drawing
  { key: 'p', description: 'Draw parcel' },
  { key: 'l', description: 'Draw line' },
  { key: 'o', description: 'Draw point' },

  // Measurement
  { key: 'm', description: 'Measure distance' },
  { key: 'M', description: 'Measure area', shift: true },

  // Tools
  { key: 'v', description: 'Select tool (default)' },
  { key: 'h', description: 'Pan/hand tool' },

  // Timeline
  { key: ' ', description: 'Play/pause timeline' },
  { key: 'ArrowLeft', description: 'Step timeline backward' },
  { key: 'ArrowRight', description: 'Step timeline forward' },
  { key: 't', description: 'Toggle timeline clock-only mode' },

  // View
  { key: 'b', description: 'Toggle left rail', ctrl: true },
  { key: 'B', description: 'Toggle right rail', ctrl: true, shift: true },

  // Search
  { key: 'k', description: 'Search entities', ctrl: true },
];
