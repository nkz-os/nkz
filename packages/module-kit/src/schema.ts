import { z } from 'zod';

const HexColor = z.string().regex(/^#[0-9A-Fa-f]{6}$/, 'must be a 6-digit hex color (e.g. #A16207)');
const SemverRange = z.string().regex(
  /^[\^~]?(\d+(\.\d+){0,2}|\d+\.\d+\.x|\d+\.x)$/,
  'must be a simple semver range like ^2.0.0, ~2.0.0, 2.x'
);
const KebabCase = z.string().regex(/^[a-z][a-z0-9]*(-[a-z0-9]+)*$/, 'must be kebab-case starting with a letter');
const RoutePath = z.string().regex(
  /^\/(?:[a-z0-9-]+(?:\/[a-z0-9-]+)*)?$/,
  'must start with / and have no consecutive slashes'
);
const ApiBasePath = z.string().regex(
  /^\/api(?:\/[a-z0-9-]+)+$/,
  'must start with /api/ and have no consecutive slashes'
);
const Lang = z.string().regex(/^[a-z]{2}$/, 'must be a 2-letter language code');

const AccentSchema = z.object({
  base: HexColor,
  soft: HexColor,
  strong: HexColor,
}).strict();

const NavigationSchema = z.object({
  section: z.enum(['modules', 'admin', 'tools']),
  priority: z.number().int().nonnegative(),
  label: z.record(Lang, z.string()).optional(),
}).strict();

const SlotEntrySchema = z.object({
  id: KebabCase,
  // component is either a React component reference (preferred, new shape) or
  // the string name of an exported component (legacy SlotWidgetDefinition shape).
  component: z.any(),
  priority: z.number().int().optional(),
  showWhen: z.any().optional(),
  defaultProps: z.record(z.string(), z.any()).optional(),
  // Legacy passthrough fields used by SlotWidgetDefinition from @nekazari/sdk.
  // toNKZRegistration prefers localComponent when both are present.
  moduleId: z.string().optional(),
  localComponent: z.any().optional(),
}).strict();

// Slot type keys are intentionally loose (z.string()) so this schema stays
// host-version-agnostic — the canonical SlotType union lives in @nekazari/sdk
// and may grow over time.
const SlotsSchema = z.record(z.string(), z.array(SlotEntrySchema));

const ApiSchema = z.object({
  basePath: ApiBasePath,
}).strict();

const DataSchema = z.object({
  entities: z.array(z.string()).optional(),
  timeseries: z.array(z.string()).optional(),
}).strict();

const I18nSchema = z.record(Lang, z.any()); // values are () => Promise<unknown> — Zod can't validate functions

// Unified viewer layers (contract frozen 2026-07-12, plan §B1) — mirrors
// ViewerLayerDecl from @nekazari/sdk. Kept as its own Zod object (Zod can't
// derive a schema from an imported TS interface) but must stay field-for-field
// identical to that type — see the registerViewerLayers() call in defineModule.ts.
const ViewerLayerDeclSchema = z.object({
  id: KebabCase,
  titleKey: z.string().min(1),
  group: z.string().optional(),
  supportsOpacity: z.boolean().optional(),
  defaultVisible: z.boolean().optional(),
}).strict();

export const ModuleDefinitionSchema = z.object({
  // Identity
  id: KebabCase,
  displayName: z.string().min(1),
  version: z.string().regex(/^\d+\.\d+\.\d+$/).optional(),
  hostApiVersion: SemverRange,
  description: z.string().optional(),

  // UI
  accent: AccentSchema,
  icon: z.string().optional(),
  main: z.any().optional(),

  // Host integration
  route: RoutePath.optional(),
  navigation: NavigationSchema.optional(),
  slots: SlotsSchema.optional(),

  // Backend
  api: ApiSchema.optional(),

  // Permissions
  requiredRoles: z.array(z.string()).optional(),
  requiredPlan: z.enum(['basic', 'pro', 'premium', 'enterprise']).optional(),

  // i18n
  i18n: I18nSchema.optional(),

  // Data dependencies
  data: DataSchema.optional(),

  // Unified viewer layers (contract frozen 2026-07-12) — registered into
  // @nekazari/sdk's LayerRegistry when the module is defined. HARD CUT:
  // no fallback to module-local layer-toggle contexts.
  viewerLayers: z.array(ViewerLayerDeclSchema).optional(),
}).strict();

export type ModuleDefinition = z.infer<typeof ModuleDefinitionSchema>;
