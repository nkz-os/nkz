/**
 * Copyright 2025-2026 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 *
 * NKZClient — HTTP client for Nekazari platform modules.
 *
 * Features:
 * - Automatic tenant + auth header injection
 * - 401 token-refresh orchestration via DOM events (host-driven)
 * - Request queuing during refresh (replays on success)
 * - httpOnly cookie support (credentials: 'include')
 * - Idempotent POST/PUT/DELETE retry on 401 (safe: api-gateway rejects before backend)
 *
 * Refresh flow:
 *   1. NKZClient receives 401 → queues request → dispatches `nekazari:token:expired`
 *   2. Host KeycloakAuthContext hears event → calls kc.updateToken() + api.setSession()
 *   3. Host dispatches `nekazari:token:refreshed` (success) or `nekazari:session:expired` (failure)
 *   4. NKZClient hears success → replays queued requests with fresh cookie
 */

// ── Module-level refresh state (shared across ALL NKZClient instances) ──

const REFRESH_EVENT = 'nekazari:token:expired';
const REFRESHED_EVENT = 'nekazari:token:refreshed';
const EXPIRED_EVENT = 'nekazari:session:expired';

let isRefreshing = false;

interface QueueItem {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}

let failedQueue: QueueItem[] = [];

function processQueue(success: boolean): void {
  const queue = failedQueue;
  failedQueue = [];
  queue.forEach(({ resolve, reject }) => {
    if (success) {
      resolve(true);
    } else {
      reject(new Error('Session expired — token refresh failed'));
    }
  });
}

function waitForRefresh(): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const onRefreshed = () => {
      cleanup();
      resolve();
    };
    const onExpired = () => {
      cleanup();
      reject(new Error('Session expired — please log in again'));
    };
    const cleanup = () => {
      window.removeEventListener(REFRESHED_EVENT, onRefreshed);
      window.removeEventListener(EXPIRED_EVENT, onExpired);
    };
    window.addEventListener(REFRESHED_EVENT, onRefreshed, { once: true });
    window.addEventListener(EXPIRED_EVENT, onExpired, { once: true });

    // Safety timeout: if no response after 15s, reject
    setTimeout(() => {
      cleanup();
      reject(new Error('Token refresh timed out'));
    }, 15000);
  });
}

export interface NKZClientOptions {
  baseUrl: string;
  getToken?: () => string | undefined;
  getTenantId?: () => string | undefined;
  defaultHeaders?: Record<string, string>;
}

export class NKZClient {
  private readonly baseUrl: string;
  private readonly getToken?: () => string | undefined;
  private readonly getTenantId?: () => string | undefined;
  private readonly defaultHeaders: Record<string, string>;

  constructor(options: NKZClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.getToken = options.getToken;
    this.getTenantId = options.getTenantId;
    this.defaultHeaders = options.defaultHeaders ?? {};
  }

  async request<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
    const method = (init.method || 'GET').toUpperCase();
    return this._doRequest<T>(path, init, method);
  }

  private async _doRequest<T = unknown>(
    path: string,
    init: RequestInit,
    method: string,
    _retryCount: number = 0,
  ): Promise<T> {
    const token = this.getToken?.();
    const tenant = this.getTenantId?.();

    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...this.defaultHeaders,
      ...(init.headers as Record<string, string> | undefined),
    };

    if (token && !headers.Authorization) {
      headers.Authorization = `Bearer ${token}`;
    }
    if (tenant && !headers['X-Tenant-ID']) {
      headers['X-Tenant-ID'] = tenant;
    }

    const url = `${this.baseUrl}${path.startsWith('/') ? '' : '/'}${path}`;

    const response = await fetch(url, {
      ...init,
      headers,
      credentials: 'include',
    });

    // ── 401: Event-based refresh orchestration with host ──
    // Only attempt refresh ONCE per request to prevent infinite loops.
    if (response.status === 401 && typeof window !== 'undefined' && _retryCount === 0) {
      return this._handle401<T>(path, init, method);
    }

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new Error(`HTTP ${response.status} ${response.statusText}: ${text}`);
    }

    // Intentar JSON, si falla devolver texto
    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return response.json() as Promise<T>;
    }
    // @ts-ignore permitir texto o vacío
    return response.text() as T;
  }

  /**
   * Handle 401: orchestrate token refresh with the host via DOM events.
   *
   * - If this is the first 401 → dispatch `nekazari:token:expired` to trigger
   *   the host's Keycloak token refresh flow.
   * - If another request is already refreshing → queue this request and wait
   *   for the host's response (`nekazari:token:refreshed` or `nekazari:session:expired`).
   * - On success → replay the original request with fresh httpOnly cookie.
   *
   * POST/PUT/DELETE are safe to retry because the api-gateway validates JWT
   * BEFORE forwarding to backend services. No duplicate mutations occur.
   */
  private async _handle401<T>(
    path: string,
    init: RequestInit,
    method: string,
    _retryCount: number = 0,
  ): Promise<T> {
    // If another request is already refreshing, queue this one
    if (isRefreshing) {
      return new Promise<T>((resolve, reject) => {
        failedQueue.push({
          resolve: () => {
            this._doRequest<T>(path, init, method, _retryCount + 1).then(resolve, reject);
          },
          reject,
        });
      });
    }

    // Start refresh orchestration
    isRefreshing = true;

    // Dispatch event to host: "we need a fresh token"
    window.dispatchEvent(new CustomEvent(REFRESH_EVENT));

    try {
      // Wait for host to complete refresh
      await waitForRefresh();

      // Refresh succeeded — replay all queued requests
      processQueue(true);
      isRefreshing = false;

      // Retry the original request with fresh cookie (retryCount=1 prevents loops)
      return this._doRequest<T>(path, init, method, _retryCount + 1);
    } catch (err) {
      // Refresh failed — reject all queued requests
      processQueue(false);
      isRefreshing = false;
      throw err;
    }
  }

  get<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
    return this.request<T>(path, { ...init, method: 'GET' });
  }

  post<T = unknown, B = unknown>(path: string, body?: B, init: RequestInit = {}): Promise<T> {
    return this.request<T>(path, {
      ...init,
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : init.body,
    });
  }

  put<T = unknown, B = unknown>(path: string, body?: B, init: RequestInit = {}): Promise<T> {
    return this.request<T>(path, {
      ...init,
      method: 'PUT',
      body: body !== undefined ? JSON.stringify(body) : init.body,
    });
  }

  patch<T = unknown, B = unknown>(path: string, body?: B, init: RequestInit = {}): Promise<T> {
    return this.request<T>(path, {
      ...init,
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : init.body,
    });
  }

  delete<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
    return this.request<T>(path, { ...init, method: 'DELETE' });
  }
}

