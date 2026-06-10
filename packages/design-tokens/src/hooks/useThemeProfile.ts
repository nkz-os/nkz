import { useThemeContext } from '../ThemeProvider';

export function useThemeProfile() {
  return useThemeContext().profile;
}
