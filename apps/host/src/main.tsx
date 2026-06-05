import React from 'react';
import * as ReactDOMClient from 'react-dom/client';
import App from './App.tsx';
import './index.css';

// Expose React globally for SDK hooks (useViewer) that access window.React.
// Required for backward compatibility with @nekazari/sdk pre-MF-native hooks.
(window as any).React = React;
// Cesium CSS is imported by Cesium-using components (CesiumMap, MobileViewer) via lazy chunks
import { ErrorBoundary } from './components/ErrorBoundary';
import { initHostI18n } from './i18n/init';
import { logger } from '@/utils/logger';


// =============================================================================
// Global Error Handlers
// =============================================================================

window.onerror = (message, source, lineno, colno, error) => {
  // Benign browser quirk when layout reads run inside ResizeObserver (uPlot, charts, flex).
  if (typeof message === 'string' && message.includes('ResizeObserver loop')) {
    return true;
  }
  logger.error('[NKZ] Uncaught error:', message, { source, lineno, colno, error });
  return false;
};
window.onunhandledrejection = (event) => {
  logger.error('[NKZ] Unhandled rejection:', event.reason);
};

// =============================================================================
// Application Bootstrap
// =============================================================================

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found');
}

const root = ReactDOMClient.createRoot(rootElement);

// Initialize i18n BEFORE rendering so modules that call addResourceBundle()
// at import time operate on an already-initialized i18next instance.
// initHostI18n is idempotent (skips if already initialized).
initHostI18n().finally(() => {
  root.render(
    <React.StrictMode>
      <ErrorBoundary componentName="Application">
        <App />
      </ErrorBoundary>
    </React.StrictMode>
  );
});
