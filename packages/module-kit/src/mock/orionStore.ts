import type { NgsiLdEntity, OrionTransport, ModuleAPITransport, FilesTransport } from '../hooks/types';

/** Mutable in-memory NGSI-LD store used by MockProvider */
export class OrionMockStore implements OrionTransport {
  private entities = new Map<string, NgsiLdEntity>();

  seed(entities: NgsiLdEntity[]): void {
    for (const e of entities) this.entities.set(e.id, structuredClone(e));
  }

  async getEntity(id: string): Promise<NgsiLdEntity> {
    const e = this.entities.get(id);
    if (!e) throw new Error(`[mock] entity not found: ${id}`);
    return structuredClone(e);
  }

  async listEntities(type: string, opts?: { q?: string; limit?: number; offset?: number }): Promise<NgsiLdEntity[]> {
    const all = Array.from(this.entities.values()).filter((e) => e.type === type);
    let filtered = all;
    if (opts?.q) {
      const m = opts.q.match(/^([a-zA-Z0-9_]+)\s*==\s*"?([^"]+)"?$/);
      if (m) {
        const [, attr, val] = m;
        filtered = all.filter((e) => String(e[attr]) === val);
      }
    }
    const offset = opts?.offset ?? 0;
    const limit = opts?.limit ?? filtered.length;
    return filtered.slice(offset, offset + limit).map((e) => structuredClone(e));
  }

  async createEntity(entity: NgsiLdEntity): Promise<void> {
    if (this.entities.has(entity.id)) throw new Error(`[mock] entity already exists: ${entity.id}`);
    this.entities.set(entity.id, structuredClone(entity));
  }

  async updateEntity(id: string, attrs: Record<string, unknown>): Promise<void> {
    const existing = this.entities.get(id);
    if (!existing) throw new Error(`[mock] entity not found: ${id}`);
    this.entities.set(id, { ...existing, ...attrs });
  }

  async deleteEntity(id: string): Promise<void> {
    if (!this.entities.delete(id)) throw new Error(`[mock] entity not found: ${id}`);
  }
}

/** In-memory module backend mock — registered handlers keyed by `METHOD path` */
export class ModuleApiMockStore implements ModuleAPITransport {
  basePath: string | null = '/api/mock';
  private handlers = new Map<string, (init?: RequestInit) => Promise<unknown>>();

  register(method: string, path: string, handler: (init?: RequestInit) => Promise<unknown>): void {
    this.handlers.set(`${method.toUpperCase()} ${path}`, handler);
  }

  async fetch<T = unknown>(path: string, init?: RequestInit): Promise<T> {
    const key = `${(init?.method ?? 'GET').toUpperCase()} ${path}`;
    const handler = this.handlers.get(key);
    if (!handler) throw new Error(`[mock] no handler for ${key}`);
    return (await handler(init)) as T;
  }
}

/** In-memory file store used by MockProvider — keyed by absolute path */
export class FilesMockStore implements FilesTransport {
  private blobs = new Map<string, Blob>();

  async upload(file: Blob, path: string): Promise<{ url: string }> {
    this.blobs.set(path, file);
    return { url: `mock://files/${path}` };
  }

  async getUrl(path: string): Promise<string> {
    if (!this.blobs.has(path)) {
      throw new Error(`[mock] file not found: ${path}`);
    }
    return `mock://files/${path}`;
  }

  async list(prefix: string): Promise<string[]> {
    return Array.from(this.blobs.keys()).filter((p) => p.startsWith(prefix));
  }
}
