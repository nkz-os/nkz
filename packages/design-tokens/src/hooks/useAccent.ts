import { useAccentContext } from '../AccentScope';

export function useAccent() {
  const accent = useAccentContext();
  if (!accent) throw new Error('useAccent must be used within <AccentScope>');
  return accent;
}
