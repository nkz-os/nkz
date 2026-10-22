/// <reference types="vite/client" />

/** Vite / build-time and runtime-injectable env vars (see config/environment.ts) */
interface ImportMetaEnv {
  readonly MODE: string;
  readonly VITE_API_URL: string;
  readonly VITE_API_TIMEOUT?: string;
  readonly VITE_API_RETRIES?: string;
  readonly VITE_KEYCLOAK_URL?: string;
  readonly VITE_KEYCLOAK_REALM?: string;
  readonly VITE_KEYCLOAK_CLIENT_ID?: string;
  readonly VITE_KEYCLOAK_REDIRECT_URI?: string;
  readonly VITE_KEYCLOAK_ADMIN_URL?: string;
  readonly VITE_CONTEXT_URL?: string;
  readonly VITE_GEOSERVER_URL?: string;
  readonly VITE_TITILER_URL?: string;
  readonly VITE_GRAFANA_URL?: string;
  readonly VITE_PROMETHEUS_URL?: string;
  readonly VITE_ROS2_BRIDGE_URL?: string;
  readonly VITE_ENABLE_I18N?: string;
  readonly VITE_ENABLE_MONITORING?: string;
  readonly VITE_ENABLE_DEBUG?: string;
  readonly VITE_ENVIRONMENT?: string;
  /** `commercial` → commercial landing; omit or other → OSS landing. Runtime: window.__ENV__. */
  readonly VITE_NKZ_EDITION?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Cesium global — full typings would require fixing CesiumMap/use3DTiles/etc. to match cesium package
interface Window {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- Cesium API varies; strict type breaks existing usage
  Cesium: any;
  __ENV__?: {
    [key: string]: unknown;
  };
  __REACT_MOUNTED__?: boolean;
  getEnvVar?: (key: string, defaultValue?: string) => string;
}
