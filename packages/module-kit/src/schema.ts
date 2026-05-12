import { z } from 'zod';

const HexColor = z.string().regex(/^#[0-9A-Fa-f]{6}$/, 'must be a 6-digit hex color (e.g. #A16207)');
const SemverRange = z.string().regex(/^[\^~]?\d+(\.\d+){0,2}(\.x)?$/, 'must be a semver range (e.g. ^2.0.0)');
const KebabCase = z.string().regex(/^[a-z][a-z0-9]*(-[a-z0-9]+)*$/, 'must be kebab-case starting with a letter');
const RoutePath = z.string().regex(/^\/[a-z0-9-/]*$/, 'must start with / and be lowercase');
const ApiBasePath = z.string().regex(/^\/api\/[a-z0-9-/]+$/, 'must start with /api/');
const Lang = z.string().regex(/^[a-z]{2}$/, 'must be a 2-letter language code');

const AccentSchema = z.object({
  base: HexColor,
  soft: HexColor,
  strong: HexColor,
});

const NavigationSchema = z.object({
  section: z.enum(['modules', 'admin', 'tools']),
  priority: z.number().int().nonnegative(),
  label: z.record(Lang, z.string()).optional(),
});

const SlotEntrySchema = z.object({
  id: KebabCase,
  // component is a runtime React component reference — Zod can't validate it; accepted as any function/object
  component: z.any(),
  priority: z.number().int().optional(),
  showWhen: z.any().optional(),
  defaultProps: z.record(z.string(), z.any()).optional(),
});

const SlotsSchema = z.record(z.string(), z.array(SlotEntrySchema));

const ApiSchema = z.object({
  basePath: ApiBasePath,
});

const DataSchema = z.object({
  entities: z.array(z.string()).optional(),
  timeseries: z.array(z.string()).optional(),
});

const I18nSchema = z.record(Lang, z.any()); // values are () => Promise<unknown> — Zod can't validate functions

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
});

export type ModuleDefinition = z.infer<typeof ModuleDefinitionSchema>;
