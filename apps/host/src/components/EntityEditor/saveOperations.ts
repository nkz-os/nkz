// =============================================================================
// Entity editor save - broker operations for the edited attributes
// =============================================================================
// PATCH /attrs only updates attributes the entity already has, so new and changed
// attributes go in one merge-patch, which creates or overwrites them. An attribute
// the user cleared is removed with DELETE on it, never with a null value.

import type { NGSAttribute } from '@/types/ngsi-ld';
import type { EditorFormState } from './types';

export type EditorSaveOperation =
  | { kind: 'merge'; fragment: Record<string, NGSAttribute> }
  | { kind: 'delete'; attribute: string };

export function planEditorSave(state: EditorFormState): EditorSaveOperation[] {
  const fragment: Record<string, NGSAttribute> = {};
  const removed: string[] = [];

  state.dirtyFields.forEach(key => {
    const current = state.attributes[key];
    if (current !== undefined) {
      fragment[key] = current;
    } else if (state.originalAttributes[key] !== undefined) {
      removed.push(key);
    }
  });

  const operations: EditorSaveOperation[] = [];
  if (Object.keys(fragment).length > 0) operations.push({ kind: 'merge', fragment });
  for (const attribute of removed) operations.push({ kind: 'delete', attribute });
  return operations;
}
