// =============================================================================
// Main App Component - Modular Architecture
// =============================================================================
// This file should ONLY contain CORE routes that are essential for the platform.
// All feature modules (NDVI, Weather, Robots, etc.) should be loaded dynamically
// from the marketplace via ModuleContext.
//
// CORE Routes (hardcoded):
// - Public: Landing, Login, Activation, ForgotPassword
// - Core Features: Dashboard, Settings
// - Admin: System Admin, Module Management
//
// Everything else should come from marketplace modules.
// =============================================================================

import React, { useState, useEffect } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from '@/context/KeycloakAuthContext';
import { I18nextProvider } from 'react-i18next';
import { i18n } from '@nekazari/sdk';
import { I18nProvider } from '@/context/I18nContext';
import { CookieConsentProvider } from '@/context/CookieConsentContext';
import { AnalyticsConsentRoot } from '@/components/AnalyticsConsentRoot';
import { ModuleProvider, useModules } from '@/context/ModuleContext';
import { ViewerProvider } from '@/context/ViewerContext';
import { ThemeProvider } from '@/context/ThemeContext';
import { ToastProvider } from '@/context/ToastContext';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { AdminRoute, FarmerRoute, ModulesRoute } from '@/components/KeycloakProtectedRoute';
import { RemoteModuleLoader } from '@/components/RemoteModuleLoader';
import { Layout } from '@/components/Layout';
const UnifiedViewer = React.lazy(() => import('@/components/UnifiedViewer').then(m => ({ default: m.UnifiedViewer })));

// CORE Pages (essential for platform operation)
const Landing = React.lazy(() => import('@/pages/Landing').then(m => ({ default: m.Landing })));
const ForgotPassword = React.lazy(() => import('@/pages/ForgotPassword').then(m => ({ default: m.ForgotPassword })));
const KeycloakLogin = React.lazy(() => import('@/pages/KeycloakLogin'));
const RegistrationWizard = React.lazy(() => import('@/pages/register/RegistrationWizard').then(m => ({ default: m.RegistrationWizard })));
const DashboardImproved = React.lazy(() => import('@/pages/DashboardImproved').then(m => ({ default: m.DashboardImproved })));
const Settings = React.lazy(() => import('@/pages/Settings').then(m => ({ default: m.Settings })));
const Modules = React.lazy(() => import('@/pages/admin/Modules').then(m => ({ default: m.Modules })));
const AdminManagement = React.lazy(() => import('@/pages/admin/AdminManagement').then(m => ({ default: m.AdminManagement })));

const Risks = React.lazy(() => import('@/pages/Risks').then(m => ({ default: m.Risks })));
const IntelligenceInfoPage = React.lazy(() => import('@/pages/IntelligenceInfoPage').then(m => ({ default: m.IntelligenceInfoPage })));
const NotFound = React.lazy(() => import('@/components/error/NotFound').then(m => ({ default: m.NotFound })));
const MobileViewer = React.lazy(() => import('@/pages/MobileViewer'));
// Route-level code splitting comment marker
const RouteFallback = () => (
  <div className="flex items-center justify-center min-h-screen bg-slate-950">
    <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
  </div>
);

import { EntityEditorModal, initEntityEditorListener, type EditorEventDetail } from '@/components/EntityEditor';


// Dynamic routes component that includes remote modules
const DynamicRoutes = () => {
  const { modules, isLoading } = useModules();

  try {
    return (
      <React.Suspense fallback={<RouteFallback />}>
        <Routes>
        {/* ============================================
            PUBLIC ROUTES (No authentication required)
            ============================================ */}
        <Route path="/" element={<Landing />} />
        <Route path="/mobile-viewer" element={<MobileViewer />} />
        <Route path="/login" element={<KeycloakLogin />} />
        <Route path="/activate" element={<RegistrationWizard defaultMethod="code" />} />
        <Route path="/register" element={<RegistrationWizard defaultMethod="otp" />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />

        {/* ============================================
            CORE PROTECTED ROUTES (Essential platform features)
            ============================================ */}
        <Route
          path="/dashboard"
          element={
            <FarmerRoute>
              <ViewerProvider>
                <DashboardImproved />
              </ViewerProvider>
            </FarmerRoute>
          }
        />

        <Route
          path="/settings"
          element={
            <FarmerRoute>
              <Settings />
            </FarmerRoute>
          }
        />

        {/* Unified Command Center - Main Viewer */}
        <Route
          path="/entities"
          element={
            <FarmerRoute>
              <ViewerProvider>
                <UnifiedViewer />
              </ViewerProvider>
            </FarmerRoute>
          }
        />

        {/* Redirect /viewer to /entities (simplification) */}
        <Route
          path="/viewer"
          element={<Navigate to="/entities" replace />}
        />

        <Route
          path="/risks"
          element={
            <FarmerRoute>
              <Layout>
                <Risks />
              </Layout>
            </FarmerRoute>
          }
        />

        {/* ============================================
            ADMIN ROUTES (Platform administration)
            ============================================ */}
        <Route
          path="/admin/management"
          element={
            <AdminRoute>
              <Layout>
                <AdminManagement />
              </Layout>
            </AdminRoute>
          }
        />

        {/* Redirect legacy admin paths to unified management */}
        <Route
          path="/system-admin"
          element={<Navigate to="/admin/management" replace />}
        />

        <Route
          path="/admin/modules"
          element={
            <ModulesRoute>
              <Layout>
                <Modules />
              </Layout>
            </ModulesRoute>
          }
        />

        {/* ============================================
            BACKEND-ONLY MODULE ROUTES (Special handling)
            ============================================ */}
        {/* Intelligence Module - Backend-only, show info page */}
        <Route
          path="/intelligence"
          element={
            <FarmerRoute>
              <Layout>
                <IntelligenceInfoPage />
              </Layout>
            </FarmerRoute>
          }
        />

        {/* ============================================
            DYNAMIC MODULE ROUTES (Loaded from marketplace)
            Note: Pages already include their own Layout
            Modules that need ViewerContext are wrapped with ViewerProvider
            ============================================ */}
        {!isLoading && Array.isArray(modules) && modules.map((module) => {
          if (!module || !module.id || !module.routePath) {
            console.warn('[DynamicRoutes] Invalid module skipped:', module);
            return null;
          }
          // Skip intelligence route (handled statically above)
          if (module.id === 'intelligence') {
            return null;
          }
          return (
            <Route
              key={module.id}
              path={module.routePath}
              element={
                <FarmerRoute>
                  <ViewerProvider>
                    <Layout fullWidth>
                      <RemoteModuleLoader module={module} />
                    </Layout>
                  </ViewerProvider>
                </FarmerRoute>
              }
            />
          );
        })}

        {/* 404 - Not Found Page */}
        <Route path="*" element={<NotFound />} />
      </Routes>
      </React.Suspense>
    );
  } catch (error) {
    console.error('🔥 [DynamicRoutes] CRITICAL RENDER ERROR:', error);
    return null;
  }
};

const AppRoutes = () => {
  try {
    return (
      <ModuleProvider>
        <DynamicRoutes />
      </ModuleProvider>
    );
  } catch (err) {
    console.error('🔥 [AppRoutes] CRITICAL ERROR:', err);
    throw err;
  }
};

const AppInitializer = () => {
  const [editorState, setEditorState] = useState<EditorEventDetail | null>(null);

  useEffect(() => {
    return initEntityEditorListener((detail) => setEditorState(detail));
  }, []);

  return (
    <>
      <AppRoutes />
      <EntityEditorModal
        entityId={editorState?.entityId || ''}
        entityType={editorState?.entityType || ''}
        isOpen={!!editorState}
        onClose={() => setEditorState(null)}
        onSuccess={() => setEditorState(null)}
      />
    </>
  );
};

// Simple fallback component that shows diagnostic info
const DiagnosticFallback: React.FC<{ error?: Error | null }> = ({ error }) => (
  <div style={{ padding: '20px', fontFamily: 'sans-serif', maxWidth: '800px', margin: '50px auto' }}>
    <h1 style={{ color: '#dc2626' }}>⚠️ Error de Inicialización</h1>
    <p>La aplicación no pudo inicializarse correctamente.</p>
    {error && (
      <div style={{ background: '#fee2e2', padding: '15px', borderRadius: '4px', marginTop: '20px', border: '1px solid #fca5a5' }}>
        <strong style={{ color: '#dc2626' }}>Error:</strong>
        <pre style={{ marginTop: '10px', fontSize: '12px', overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {error.message}
          {error.stack && `\n\n${error.stack}`}
        </pre>
      </div>
    )}
    <div style={{ marginTop: '20px', padding: '15px', background: '#f0f9ff', borderRadius: '4px' }}>
      <strong>Diagnóstico:</strong>
      <ul style={{ marginTop: '10px' }}>
        <li>window.__ENV__: {typeof window !== 'undefined' && window.__ENV__ ? '✅ Disponible' : '❌ No disponible'}</li>
        <li>React: {React ? '✅ Cargado' : '❌ No cargado'}</li>
        <li>Root element: {document.getElementById('root') ? '✅ Existe' : '❌ No existe'}</li>
      </ul>
    </div>
    <button
      onClick={() => window.location.reload()}
      style={{
        marginTop: '20px',
        padding: '10px 20px',
        background: '#2563eb',
        color: 'white',
        border: 'none',
        borderRadius: '4px',
        cursor: 'pointer',
        fontSize: '16px'
      }}
    >
      🔄 Recargar Página
    </button>
  </div>
);

// Render function for ErrorBoundary fallback
const renderFallback = (error: Error | null) => <DiagnosticFallback error={error} />;

function App() {
  try {
    return (
      <BrowserRouter>
        <ErrorBoundary
          componentName="App"
          fallback={renderFallback}
        >
          <ErrorBoundary componentName="AuthProvider" fallback={renderFallback}>
            <AuthProvider>
              <ErrorBoundary componentName="I18nextProvider" fallback={renderFallback}>
                <I18nextProvider i18n={i18n}>
                  <ErrorBoundary componentName="I18nProvider" fallback={renderFallback}>
                    <I18nProvider>
                      <CookieConsentProvider>
                        <AnalyticsConsentRoot />
                        <ErrorBoundary componentName="ThemeProvider" fallback={renderFallback}>
                          <ThemeProvider>
                            <ErrorBoundary componentName="ToastProvider" fallback={renderFallback}>
                              <ToastProvider>
                                <ErrorBoundary componentName="AppInitializer" fallback={renderFallback}>
                                  <AppInitializer />
                                </ErrorBoundary>
                              </ToastProvider>
                            </ErrorBoundary>
                          </ThemeProvider>
                        </ErrorBoundary>
                      </CookieConsentProvider>
                    </I18nProvider>
                  </ErrorBoundary>
                </I18nextProvider>
              </ErrorBoundary>
            </AuthProvider>
          </ErrorBoundary>
        </ErrorBoundary>
      </BrowserRouter>
    );
  } catch (error) {
    console.error('[App] Error in render:', error);
    return <DiagnosticFallback error={error instanceof Error ? error : new Error(String(error))} />;
  }
}

export default App;
