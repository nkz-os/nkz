/**
 * Copyright 2025 NKZ Platform (Nekazari)
 * Licensed under Apache-2.0
 *
 * Client for the timeseries-reader observation endpoints
 * (services/timeseries-reader/observations/routes.py):
 *
 *   POST /api/observations/best        best observation per bucket (tiered, never fused across groups)
 *   POST /api/observations             raw stored observations, cursor-paged (CSV is not exposed here)
 *   GET  /api/observations/properties  the observable-property catalogue
 *
 * Auth is the session cookie (`credentials: 'include'`) against the runtime API origin
 * (see ./apiOrigin). Wire names are camelCase and kept as-is. Timestamps stay ISO-8601
 * strings (UTC, trailing Z) exactly as the server sends them.
 *
 * Errors: non-2xx -> ObservationsError(status, message); a 2xx whose body is not JSON or
 * does not match the expected shape, or misbehaving raw paging -> ObservationsParseError
 * (a subclass, status 502). Network failures (fetch rejecting) propagate unchanged.
 */

import { toApiUrl } from './apiOrigin';

// ------------------------------------------------------------------ errors

export class ObservationsError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ObservationsError';
    this.status = status;
    Object.setPrototypeOf(this, new.target.prototype);
  }
}

export class ObservationsParseError extends ObservationsError {
  constructor(message: string) {
    super(502, message);
    this.name = 'ObservationsParseError';
  }
}

// ------------------------------------------------------------------ types

export type ObservationMode = 'series' | 'latest';
export type ObservationResolution = 'raw' | 'PT15M' | 'PT1H' | 'P1D';
export type ObservationTiers = 'all' | 'observed';

/** Request sub-context filter (note: `height`, whereas responses carry `heightM`). */
export interface SubContextFilter {
  depthFrom?: number;
  depthTo?: number;
  height?: number;
  organ?: string;
}

/** Group sub-context as returned by `best` (`heightM`, not `height`). Passed through untouched. */
export type GroupSubContext = Record<string, unknown>;

export interface ObservationSource {
  kind: string;
  /** null/undefined when the server has no id; never "". */
  id?: string | null;
  /** Present only for device or manual sources (not for kind "model"). */
  deploymentId?: string | null;
  distanceM?: number | null;
  n?: number | null;
}

export interface ObservationPoint {
  t: string;
  value: number | null;
  tier: number | null;
  fidelity: string;
  confidence: string;
  calibrated: boolean;
  mediumFallback: boolean;
  coverage: number;
  sources: ObservationSource[];
}

export interface ObservationGroup {
  subContext: GroupSubContext;
  points: ObservationPoint[];
}

export interface BestResult {
  feature: string | null;
  property: string;
  unitCode: string | null;
  unitSymbol: string | null;
  medium: string;
  mode: string;
  resolution: string;
  tz: string;
  from: string;
  to: string;
  groups: ObservationGroup[];
  tiersUsed: number[];
  warnings: string[];
}

/** One stored, unfused observation (raw endpoint). Sub-context is flattened. */
export interface Observation {
  t: string;
  value: number;
  observedProperty: string;
  featureOfInterest: string | null;
  medium: string | null;
  depthFromCm: number | null;
  depthToCm: number | null;
  heightM: number | null;
  organ: string | null;
  contextRef: string | null;
  deploymentId: string;
  sourceKind: string | null;
  /** GeoJSON geometry or null. */
  location: Record<string, unknown> | null;
  timeSource: string | null;
  qualityFlag: string | null;
  calibrated: boolean;
  deviceId: string | null;
  tier: number | null;
  distanceM: number | null;
}

/** A catalogue row (observation_properties) plus the derived UN/CEFACT `unitCode`. */
export interface ObservableProperty {
  id: string;
  tenantId: string | null;
  name: string;
  domain: string;
  unit: string;
  unitCode: string | null;
  defaultMedium: string;
  allowedMedia: string[];
  temporalSemantics: string;
  validMin: number | null;
  validMax: number | null;
  maxRatePerHour: number | null;
  fusion: string;
  nearbyRadiusM: number | null;
  maxAgeS: number | null;
  minCoverage: number | null;
  subContextTolerance: Record<string, unknown>;
  hasModel: boolean;
  deprecatedAt: string | null;
  createdAt: string | null;
}

/** A point in time: a Date, or an ISO-8601 string that carries a timezone. */
export type Instant = Date | string;

/** Filters shared by `best` and `raw`. Exactly one of `feature` / `geometry`. */
export interface ObservationFilters {
  feature?: string;
  /** GeoJSON Polygon or MultiPolygon. */
  geometry?: Record<string, unknown>;
  property: string;
  from: Instant;
  to: Instant;
  medium?: string;
  subContext?: SubContextFilter;
  tiers?: ObservationTiers;
  allowMediumFallback?: boolean;
}

export interface BestRequest extends ObservationFilters {
  mode?: ObservationMode;
  resolution?: ObservationResolution;
  /** IANA zone, used by P1D buckets. Default "UTC". */
  tz?: string;
}

export interface LatestRequest {
  feature: string;
  property: string;
  medium?: string;
  subContext?: SubContextFilter;
}

export interface RawRequest extends ObservationFilters {
  /** Page size (server caps at 10 000). */
  limit?: number;
  qualityFlags?: string[];
}

// -------------------------------------------------------------- helpers

/** The server validates from < to even for mode "latest" (it then substitutes the
 *  property's max_age window). The client sends this nominal window. */
const LATEST_NOMINAL_WINDOW_MS = 60 * 60 * 1000;
export const MAX_RAW_PAGES = 100_000;

const TZ_SUFFIX = /(Z|[+-]\d{2}(:?\d{2})?)$/i;

function isoZ(value: Instant, name: string): string {
  let d: Date;
  if (value instanceof Date) {
    d = value;
  } else {
    if (typeof value !== 'string' || !TZ_SUFFIX.test(value)) {
      throw new TypeError(`${name} must carry a timezone (use Z for UTC)`);
    }
    d = new Date(value);
  }
  if (Number.isNaN(d.getTime())) {
    throw new TypeError(`${name} is not a valid datetime`);
  }
  return d.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function filtersBody(f: ObservationFilters): Record<string, unknown> {
  const hasFeature = f.feature != null;
  const hasGeometry = f.geometry != null;
  if (hasFeature === hasGeometry) {
    throw new TypeError('exactly one of feature or geometry is required');
  }
  const body: Record<string, unknown> = {
    property: f.property,
    from: isoZ(f.from, 'from'),
    to: isoZ(f.to, 'to'),
    tiers: f.tiers ?? 'all',
    allowMediumFallback: Boolean(f.allowMediumFallback),
  };
  if (hasFeature) body.feature = f.feature;
  else body.geometry = f.geometry;
  if (f.medium != null) body.medium = f.medium;
  if (f.subContext && Object.keys(f.subContext).length > 0) {
    body.subContext = { ...f.subContext };
  }
  return body;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function fail(what: string): never {
  throw new ObservationsParseError(`malformed ${what} in reader response`);
}

function req<T>(d: Record<string, unknown>, key: string, type: 'string' | 'number', what: string): T {
  const v = d[key];
  if (typeof v !== type) fail(`${what}.${key}`);
  return v as T;
}

function optStr(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

function optNum(v: unknown): number | null {
  return typeof v === 'number' ? v : null;
}

function list(v: unknown, what: string): unknown[] {
  if (v == null) return [];
  if (!Array.isArray(v)) fail(what);
  return v;
}

function parseSource(raw: unknown): ObservationSource {
  if (!isObj(raw)) fail('source');
  const src: ObservationSource = {
    kind: req<string>(raw, 'kind', 'string', 'source'),
    // an absent id is null, never ""
    id: typeof raw.id === 'string' && raw.id !== '' ? raw.id : null,
  };
  if (typeof raw.deploymentId === 'string') src.deploymentId = raw.deploymentId;
  else if (raw.deploymentId === null) src.deploymentId = null;
  if (typeof raw.distanceM === 'number') src.distanceM = raw.distanceM;
  else if (raw.distanceM === null) src.distanceM = null;
  if (typeof raw.n === 'number') src.n = raw.n;
  else if (raw.n === null) src.n = null;
  return src;
}

function parsePoint(raw: unknown): ObservationPoint {
  if (!isObj(raw)) fail('point');
  return {
    t: req<string>(raw, 't', 'string', 'point'),
    value: optNum(raw.value),
    tier: optNum(raw.tier),
    fidelity: req<string>(raw, 'fidelity', 'string', 'point'),
    confidence: req<string>(raw, 'confidence', 'string', 'point'),
    calibrated: Boolean(raw.calibrated),
    mediumFallback: Boolean(raw.mediumFallback),
    coverage: typeof raw.coverage === 'number' ? raw.coverage : 0,
    sources: list(raw.sources, 'point.sources').map(parseSource),
  };
}

function parseGroup(raw: unknown): ObservationGroup {
  if (!isObj(raw)) fail('group');
  if (raw.subContext != null && !isObj(raw.subContext)) fail('group.subContext');
  return {
    subContext: { ...((raw.subContext as Record<string, unknown> | null | undefined) ?? {}) },
    points: list(raw.points, 'group.points').map(parsePoint),
  };
}

function parseBest(raw: unknown): BestResult {
  if (!isObj(raw)) fail('best result');
  return {
    feature: optStr(raw.feature),
    property: req<string>(raw, 'property', 'string', 'best'),
    unitCode: optStr(raw.unitCode),
    unitSymbol: optStr(raw.unitSymbol),
    medium: req<string>(raw, 'medium', 'string', 'best'),
    mode: req<string>(raw, 'mode', 'string', 'best'),
    resolution: req<string>(raw, 'resolution', 'string', 'best'),
    tz: req<string>(raw, 'tz', 'string', 'best'),
    from: req<string>(raw, 'from', 'string', 'best'),
    to: req<string>(raw, 'to', 'string', 'best'),
    groups: list(raw.groups, 'best.groups').map(parseGroup),
    tiersUsed: list(raw.tiersUsed, 'best.tiersUsed').map((x) => {
      if (typeof x !== 'number') fail('best.tiersUsed');
      return x;
    }),
    warnings: list(raw.warnings, 'best.warnings').map(String),
  };
}

function parseObservation(raw: unknown): Observation {
  if (!isObj(raw)) fail('observation');
  const loc = raw.location;
  if (loc != null && !isObj(loc)) fail('observation.location');
  return {
    t: req<string>(raw, 't', 'string', 'observation'),
    value: req<number>(raw, 'value', 'number', 'observation'),
    observedProperty: req<string>(raw, 'observedProperty', 'string', 'observation'),
    featureOfInterest: optStr(raw.featureOfInterest),
    medium: optStr(raw.medium),
    depthFromCm: optNum(raw.depthFromCm),
    depthToCm: optNum(raw.depthToCm),
    heightM: optNum(raw.heightM),
    organ: optStr(raw.organ),
    contextRef: optStr(raw.contextRef),
    deploymentId: req<string>(raw, 'deploymentId', 'string', 'observation'),
    sourceKind: optStr(raw.sourceKind),
    location: (loc as Record<string, unknown> | null | undefined) ?? null,
    timeSource: optStr(raw.timeSource),
    qualityFlag: optStr(raw.qualityFlag),
    calibrated: Boolean(raw.calibrated),
    deviceId: optStr(raw.deviceId),
    tier: optNum(raw.tier),
    distanceM: optNum(raw.distanceM),
  };
}

function parseProperty(raw: unknown): ObservableProperty {
  if (!isObj(raw)) fail('property');
  if (raw.subContextTolerance != null && !isObj(raw.subContextTolerance)) {
    fail('property.subContextTolerance');
  }
  return {
    id: req<string>(raw, 'id', 'string', 'property'),
    tenantId: optStr(raw.tenantId),
    name: req<string>(raw, 'name', 'string', 'property'),
    domain: req<string>(raw, 'domain', 'string', 'property'),
    unit: req<string>(raw, 'unit', 'string', 'property'),
    unitCode: optStr(raw.unitCode),
    defaultMedium: req<string>(raw, 'defaultMedium', 'string', 'property'),
    allowedMedia: list(raw.allowedMedia, 'property.allowedMedia').map(String),
    temporalSemantics: req<string>(raw, 'temporalSemantics', 'string', 'property'),
    validMin: optNum(raw.validMin),
    validMax: optNum(raw.validMax),
    maxRatePerHour: optNum(raw.maxRatePerHour),
    fusion: typeof raw.fusion === 'string' ? raw.fusion : 'median',
    nearbyRadiusM: optNum(raw.nearbyRadiusM),
    maxAgeS: optNum(raw.maxAgeS),
    minCoverage: optNum(raw.minCoverage),
    subContextTolerance: { ...((raw.subContextTolerance as Record<string, unknown> | null | undefined) ?? {}) },
    hasModel: Boolean(raw.hasModel),
    deprecatedAt: optStr(raw.deprecatedAt),
    createdAt: optStr(raw.createdAt),
  };
}

async function errorMessage(resp: Response): Promise<string> {
  let text = '';
  try {
    text = await resp.text();
  } catch {
    // fall through to statusText
  }
  try {
    const payload: unknown = JSON.parse(text);
    if (isObj(payload)) {
      const m = payload.error ?? payload.message;
      if (m) return String(m);
    }
  } catch {
    // not JSON: use the raw text
  }
  return text.slice(0, 500) || resp.statusText || `HTTP ${resp.status}`;
}

async function call(method: 'GET' | 'POST', path: string, body?: unknown): Promise<unknown> {
  const init: RequestInit = { method, credentials: 'include', headers: { Accept: 'application/json' } };
  if (body !== undefined) {
    init.headers = { ...init.headers, 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(toApiUrl(path), init);
  if (!resp.ok) throw new ObservationsError(resp.status, await errorMessage(resp));
  try {
    return await resp.json();
  } catch {
    throw new ObservationsParseError('reader response is not valid JSON');
  }
}

/** Validate a raw page and return the next cursor (null = done). Throws on a repeated
 *  cursor or a runaway page count instead of looping forever. */
function nextCursor(page: unknown, seen: Set<string>): string | null {
  if (!isObj(page) || (page.items != null && !Array.isArray(page.items))) fail('raw page');
  const cursor = page.nextCursor;
  if (cursor == null || cursor === '') return null;
  if (typeof cursor !== 'string') throw new ObservationsParseError('nextCursor is not a string');
  if (seen.has(cursor)) {
    throw new ObservationsParseError(`repeated nextCursor ${JSON.stringify(cursor)}: aborting raw paging`);
  }
  if (seen.size >= MAX_RAW_PAGES) {
    throw new ObservationsParseError(`more than ${MAX_RAW_PAGES} raw pages: aborting`);
  }
  seen.add(cursor);
  return cursor;
}

// ------------------------------------------------------------------ API

async function best(r: BestRequest): Promise<BestResult> {
  const body = {
    ...filtersBody(r),
    mode: r.mode ?? 'series',
    resolution: r.resolution ?? 'PT1H',
    tz: r.tz ?? 'UTC',
  };
  return parseBest(await call('POST', '/api/observations/best', body));
}

async function latest(r: LatestRequest): Promise<BestResult> {
  const to = new Date();
  const from = new Date(to.getTime() - LATEST_NOMINAL_WINDOW_MS);
  return best({
    feature: r.feature,
    property: r.property,
    medium: r.medium,
    subContext: r.subContext,
    from,
    to,
    mode: 'latest',
  });
}

/** Every stored observation, unfused. Follows `nextCursor` until null: a page may be
 *  short or empty while a cursor is still present. */
async function* raw(r: RawRequest): AsyncGenerator<Observation, void, undefined> {
  const base = filtersBody(r);
  if (r.limit != null) base.limit = r.limit;
  if (r.qualityFlags != null) base.qualityFlags = [...r.qualityFlags];
  let cursor: string | null = null;
  const seen = new Set<string>();
  for (;;) {
    const body = cursor ? { ...base, cursor } : base;
    const page = await call('POST', '/api/observations', body);
    const next = nextCursor(page, seen);
    for (const item of list((page as Record<string, unknown>).items, 'raw page.items')) {
      yield parseObservation(item);
    }
    if (next === null) return;
    cursor = next;
  }
}

async function properties(): Promise<ObservableProperty[]> {
  const page = await call('GET', '/api/observations/properties');
  if (!isObj(page)) fail('properties');
  return list(page.items, 'properties.items').map(parseProperty);
}

export const observations = { best, latest, raw, properties };
