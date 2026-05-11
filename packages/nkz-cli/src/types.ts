export interface ValidationError {
  file: string;
  line?: number;
  message: string;
  severity: 'error' | 'warning';
}
