import React from 'react';
import * as ReactDOMClient from 'react-dom/client';
import * as ReactDOM from 'react-dom';
import * as RRD from 'react-router-dom';
import * as NKZSdk from '@nekazari/sdk';
import * as UIKit from '@nekazari/ui-kit';
import * as DesignTokens from '@nekazari/design-tokens';
import { ThemeProvider } from '@nekazari/design-tokens';
import { useViewerTheme } from './hooks/useViewerTheme';
import App from './App.tsx';
import './index.css';
import 'cesium/Build/Cesium/Widgets/widgets.css';
import '@nekazari/design-tokens/css';
import { ErrorBoundary } from './components/ErrorBoundary';
import { initNKZRuntime } from './utils/nkzRuntime';

// =============================================================================
// NKZ Runtime Initialization
// =============================================================================
// Expose shared dependencies via window globals for IIFE module bundles.
// Modules use these as externals in their Vite config (build.rollupOptions.external).
// This MUST run before any module scripts are loaded.

// React (modules use: external "react" → window.React)
(window as any).React = React;
(window as any).ReactDOM = { ...ReactDOM, ...ReactDOMClient };
(window as any).ReactRouterDOM = RRD;

// SDK & UI Kit (modules use: external "@nekazari/sdk" → window.__NKZ_SDK__)
(window as any).__NKZ_SDK__ = NKZSdk;
(window as any).__NKZ_UI__ = UIKit;

// Design tokens (modules use: external "@nekazari/design-tokens" → window.__NKZ_THEME__)
(window as any).__NKZ_THEME__ = DesignTokens;

// Viewer kit (modules use: external "@nekazari/viewer-kit" → window.__NKZ_VIEWER__)
import * as ViewerKit from '@nekazari/viewer-kit';
(window as any).__NKZ_VIEWER__ = ViewerKit;

// Initialize the module registration runtime (window.__NKZ__)
initNKZRuntime();

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

// ViewerThemeWrapper — provides ThemeProvider with toggleable viewer profile
function ViewerThemeWrapper({ children }: { children: React.ReactNode }) {
  const { profile, toggle } = useViewerTheme();
  return (
    <ThemeProvider profile={profile} onChange={toggle}>
      {children}
    </ThemeProvider>
  );
}

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Root element #root not found');
}

const root = ReactDOMClient.createRoot(rootElement);

root.render(
  <React.StrictMode>
    <ErrorBoundary componentName="Application">
      <ViewerThemeWrapper>
        <App />
      </ViewerThemeWrapper>
    </ErrorBoundary>
  </React.StrictMode>
);
