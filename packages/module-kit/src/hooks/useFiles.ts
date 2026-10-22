import { useNKZRuntime } from '../runtime/NKZContext';
import type { FilesTransport } from './types';

/**
 * File storage hook. Returns the same shape against the real gateway (presigned
 * MinIO URLs scoped to `tenants/<tenant>/modules/<module>/`) and against the
 * mock provider (in-memory Blob store).
 *
 * @example
 *   const { upload, getUrl } = useFiles();
 *   const { url } = await upload(file, 'reports/2026/foo.pdf');
 */
export function useFiles(): FilesTransport {
  return useNKZRuntime().files;
}
