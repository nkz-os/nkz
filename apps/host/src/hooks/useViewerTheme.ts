// =============================================================================
// useViewerTheme -- DEPRECATED: use useViewerProfile from ThemeContext instead
// =============================================================================
// This hook is kept as a re-export for backward compatibility.
// New code should use `useViewerProfile` from '@/context/ThemeContext'.

import { useViewerProfile } from '@/context/ThemeContext';

export { useViewerProfile as useViewerTheme };
