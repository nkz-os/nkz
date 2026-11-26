# Crop catalogue and cycle contract (SDK)

Readers call `fetch_crop_timeline(tenant, parcel, at)` (dict or `None`) or `load_parcel_crops` +
`resolve_parcel_crops(data, at)` (typed). `load_crop_data` / `resolve_crop_cycles` stay for the legacy shape.

## Shapes read
- **Legacy:** one `AgriCrop` per cycle with `hasAgriParcel` (alias `refAgriParcel`), dates and status on the crop.
  Sowings never carry a crop link.
- **Catalogue:** `AgriCrop` is generic (type `AgriCrop`, no parcel link, EPPO code in `relatedSource`, alias
  `eppoCode`). The cycle is the sowing `AgriParcelOperation` (`hasAgriCrop`; plan = same operation in status
  `planned`). Variety = `hasAgriProductType` -> `AgriProductType.name`. Ends link the sowing with
  `hasSowingOperation`. Declared history: `AgriParcel.declaredCampaigns` (`campaign`, `crop`, `variety`,
  `yieldKgHa`, `kind`), a list or one bare object. Done operations: status `completed` or `finished`.
- Attributes are read as short name, `nkz:` term or full IRI.

## Reading (what is requested)
- Queries: `hasAgriParcel=="<urn>"` for crops, plus `;operationType=="sowing","harvesting","tillage"` for
  operations, paged by 500. No `q` grows with the data.
- **Candidate new shape** = some sowing has a crop link, or `declaredCampaigns` is non-empty. Not a candidate:
  zero extra reads (no crops, no products), exactly the legacy behaviour.
- Candidate: every distinct crop target and product is read by id, once. The parcel's own `hasAgriCrop` is never read.
- A crop link is sent to the broker only if it matches `^urn:ngsi-ld:[A-Za-z][A-Za-z0-9]*:[A-Za-z0-9._:%-]+$`.
- Crop of a sowing or of a declared entry: 404, invalid URN or not an `AgriCrop` -> `CropDataUnavailable`.
- Crop of a harvest/tillage: same problems -> WARNING, link dropped (may be a deleted legacy cycle).
- Variety 404 or invalid URN -> WARNING, `variety` is `None`. Any other read error -> unavailable.
- Parcel 404 -> `ParcelNotFound`; 4xx other than 404/408/429 -> `CropDataRejected`; the rest, timeouts and
  non-JSON -> `CropDataUnavailable` (all subclasses of `CropDataUnavailable`, defined in `crop_cycles`).
- `load_crop_data` returns the legacy 3-tuple; for a catalogue parcel it raises `CatalogueShapeError`
  ("catalogue shape: use load_parcel_crops").

## Returns None vs raises
`fetch_crop_timeline` returns `None` (logged) on any `CropDataUnavailable`, including malformed data found while
resolving. `load_parcel_crops` / `resolve_parcel_crops` raise. Malformed: declared `campaign` not an integer
1900-2200; completed catalogue sowing without date.

## Timeline payload
Unchanged keys: `parcel_id, at, campaign, current, previous, next, pending, current_harvest_campaign,
accumulation, cycles, unassigned_operations, hints`; per cycle `crop_id, role, species, status, lifecycle, start,
end, campaign_label, pending, declared_campaign, planned_start, planned_end, variety, declared_yield_kg_ha,
harvested_weight_kg, harvests, harvest_campaigns`. `crop_id` is opaque: the opening operation's id (the plan's id
when a sowing completed it) or `{parcel}#declared-{campaign}`.

New per-cycle keys (always present; legacy values in brackets):
- `kind`: `crop` | `fallow` | `unidentified` (legacy: `crop`)
- `crop` (catalogue URN), `crop_name` (legacy: `null`)
- `irrigation_regime`, `termination_method`, `season` (legacy: from the crop when present)
- `species_lifecycle`: `annual` | `perennial` | `null`, from the species table; `lifecycle` is untouched
- `planned_sowing_end`: ISO date or `null`

## Status table
| Data | `status` |
|---|---|
| operation `planned` | `planned` |
| sown/declared, no end | `active` |
| end by harvest | `harvested` |
| end by tillage or uprooting | `terminated` |
| declared past campaign | `harvested` |
| `cancelled` operation | no cycle |
| declared fallow | `planned` until 31 Jul of its campaign, `active` while current, then `terminated` |

## Rules as implemented
1. Precedence: with the catalogue shape the parcel's legacy `AgriCrop` are ignored.
2. A sowing with a catalogue crop opens a cycle. One without crop (machine) matches by window, as today.
3. Same crop and role, within 60 days of the FIRST sowing, no end operation between -> one cycle; id = the first;
   start = the last date. A declared planting and a recorded sowing group together; the latest decides the provenance.
4. A sowing in the window of a plan of the same crop completes it (id = the plan's). Another crop leaves the plan pending.
5. Provenance: done = `actual`; done + `plantingDateSource=manual` = `declared` (planned window ignored); plan = `planned`.
6. End: `hasSowingOperation` picks the cycle even outside its window; otherwise the legacy window rules plus the
   standing-annual rule. Only covers/catch crops end by roller or mowing; only uprooting closes a stand; a harvest
   never closes a perennial, including an unknown-lifecycle perennial species. Ends before the start are unassigned.
7. No end operation: `expectedTerminationDate` is the planned end.
8. Fallow: current from 1 Aug of the previous year to 31 Jul of its campaign if nothing is sown; a sowing replaces it.
9. `unidentified`: the CURRENT main, non-fallow cycle without an EPPO code (`species` = `null`). Past cycles and
   covers keep `species` = crop name. The legacy shape never emits it.
10. Queries filter by operation class and page.

## Writers
`crops.ensure_crop(client, tenant, code_or_name, *, role)` and `ensure_variety(client, tenant, crop_key, name)`:
POST, 409 is success (never patched), other errors raise. Tenant must match `^[a-z0-9_-]+$`. A main crop needs an
EPPO code; free text is only for cover/catch crops (`x-{slug}`). `ModuleActivation.ensure_entities` logs a WARNING for
type `AgriCrop` (deprecated) but still creates it.

## Known differences from the legacy shape
- A cycle sown through field operations past its planned end, unreported: `previous` before, `current` now.
- A sowing outside every plan window opens its own cycle (was unassigned); the plan stays pending.
- On the harvest day the cycle stays `current` (status `harvested`).
- A harvest no longer closes an unknown-lifecycle perennial species.
- No equivalent for `actualPlantingDate`, `actualTerminationDate`, `terminationDate` or closed status without an end.
- Legacy-only parcels give today's timeline (differential test against the previous resolver and broker calls).
