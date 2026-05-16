import React from 'react';
import * as ReactDOMClient from 'react-dom/client';
import App from './App.tsx';
import './index.css';
// Cesium CSS is imported by Cesium-using components (CesiumMap, MobileViewer) via lazy chunks
import { ErrorBoundary } from './components/ErrorBoundary';

// =============================================================================
// Global Error Handlers
// =============================================================================

window.onerror = (message, source, lineno, colno, error) => {
  // Benign browser quirk when layout reads run inside ResizeObserver (uPlot, charts, flex).
  if (typeof message === 'string' && message.includes('ResizeObserver loop')) {
    return true;
  }
  console.error('[NKZ] Uncaught error:', message, { source, lineno, colno, error });
  return false;
};
window.onunhandledrejection = (event) => {
  console.error('[NKZ] Unhandled rejection:', event.reason);
};

// =============================================================================
// Application Bootstrap
// =============================================================================

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found');
}

const root = ReactDOMClient.createRoot(rootElement);

root.render(
  <React.StrictMode>
    <ErrorBoundary componentName="Application">
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
