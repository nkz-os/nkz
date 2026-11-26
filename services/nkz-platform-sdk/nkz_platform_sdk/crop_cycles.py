"""Crop cycles and campaigns — the single resolution of a parcel's crop timeline.

A crop cycle is one AgriCrop (main, cover or catch crop) from sowing to harvest or
termination. Every boundary carries its provenance: what was done in the field
(``actual``) beats what a person declared (``declared``), which beats a plan
(``planned``). The campaign is the calendar year; a cycle belongs to the
campaign of the year it ends in. A past crop a farmer declared with only its
campaign (``declaredCampaign``, no dates) is a closed cycle of that campaign:
it counts as history, never as current, next or pending. A perennial cycle runs
from planting to uprooting; its harvests are yearly events grouped into harvest
campaigns labelled by the year the harvest starts. A harvest outside every planned
window closes the annual main crop standing that day. Pure function: no
I/O, deterministic.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
import logging
import math
from typing import Any

from nkz_platform_sdk.crops import eppo_of, is_fallow, species_lifecycle

logger = logging.getLogger(__name__)


class CropDataUnavailable(Exception):
    """The broker could not answer for this parcel, or its data cannot be read as a timeline."""


class ParcelNotFound(CropDataUnavailable):
    """The parcel does not exist in this tenant."""


class CropDataRejected(CropDataUnavailable):
    """The broker answered but refused the request (4xx other than 404/408/429):
    a request bug, not an outage."""

ASSIGN_TOLERANCE_DAYS = 60
# An annual main crop stands at most this long after its sowing: a later harvest is not its own.
MAX_ANNUAL_STANDING_DAYS = 366
# "finished" is the official SDM value for a done operation, "completed" the one written today.
# TODO(operations block): move to the operation-class term when that block defines it.
_DONE = frozenset({"completed", "finished"})
_CLOSED = {"harvested", "terminated"}
_EXCLUDED = {"cancelled"}
_FACTS = ("actual", "declared")  # a sowing that happened (field op) or a person stated
# A campaign is over from this month of its own year: the same rollover the
# planning screens use to move on to the next campaign.
DECLARED_ROLLOVER_MONTH = 7
_AUTUMN_SOWING_MONTH = 8  # an annual sown from August on is harvested the next year
_TERMINATING_TILLAGE = frozenset({"roller_crimper", "mowing"})  # field-operations tillageType values that end a cover
_TERMINABLE_ROLES = frozenset({"cover_crop", "catch_crop"})
_PERENNIAL, _ANNUAL = "perennial", "annual"
# Private keys the catalogue adapter (resolve_parcel_crops) puts on the dicts it hands to the resolver.
_PERENNIAL_SPECIES_KEY = "_nkzAssignPerennial"  # crop: unknown lifecycle of a perennial species
_LINKED_CYCLE_KEY = "_nkzCycle"  # operation: the cycle its hasSowingOperation points to
# field-operations tillageType that pulls a perennial stand out: the only thing that closes it.
UPROOTING = "uprooting"
# A gap longer than this between two harvests of a perennial starts a new campaign.
HARVEST_CAMPAIGN_GAP_DAYS = 120
# Producers write ``nkz:declaredCampaign``; Orion returns the expanded IRI.
_DECLARED_CAMPAIGN_TERMS = ("https://nkz-os.org/ns/declaredCampaign", "nkz:declaredCampaign", "declaredCampaign")
_DECLARED_YIELD_TERMS = (
    "https://nkz-os.org/ns/declaredYieldKgHa",
    "nkz:declaredYieldKgHa",
    "declaredYieldKgHa",
)
# Variety is a platform extension attribute on AgriCrop (like cropRole and status), not an SDM field.
_VARIETY_TERMS = (
    "https://nkz-os.org/ns/variety",
    "nkz:variety",
    "variety",
    "https://uri.etsi.org/ngsi-ld/default-context/variety",
)
_HARVESTED_WEIGHT_TERMS = (
    "harvestedWeight",
    "https://smartdatamodels.org/dataModel.Agrifood/harvestedWeight",
    "https://uri.etsi.org/ngsi-ld/default-context/harvestedWeight",
)


def _value(attr: Any) -> Any:
    """keyValues or normalized NGSI-LD attribute -> plain value (unwraps @value)."""
    if isinstance(attr, dict):
        if "@value" in attr:
            return attr["@value"]
        if "object" in attr:
            return attr["object"]
        if "value" in attr:
            return _value(attr["value"])
    return attr


def _as_date(attr: Any) -> date | None:
    v = _value(attr)
    if not isinstance(v, str) or len(v) < 10:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def _declared_campaign(entity: dict) -> int | None:
    """Harvest year a person declared for a crop without dates; None when absent
    or not a plain integer (bool, float, string are treated as absent)."""
    for term in _DECLARED_CAMPAIGN_TERMS:
        v = _value(entity.get(term))
        if isinstance(v, int) and not isinstance(v, bool):
            return v
    return None


def _first(entity: dict, *names: str) -> Any:
    for n in names:
        v = _value(entity.get(n))
        if v not in (None, ""):
            return v
    return None


def _positive_float(val: Any) -> float | None:
    if isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val) and val > 0:
        return float(val)
    return None


def _declared_yield(entity: dict) -> float | None:
    for term in _DECLARED_YIELD_TERMS:
        v = _value(entity.get(term))
        pf = _positive_float(v)
        if pf is not None:
            return round(pf, 2)
    return None


def _variety(entity: dict) -> str | None:
    v = _first(entity, *_VARIETY_TERMS)
    if isinstance(v, str):
        return v.strip() or None
    return None


def _op_harvested_weight(op: dict) -> float | None:
    if _value(op.get("operationType")) != "harvesting":
        return None
    for term in _HARVESTED_WEIGHT_TERMS:
        v = _value(op.get(term))
        pf = _positive_float(v)
        if pf is not None:
            return pf
    return None


@dataclass(frozen=True)
class Boundary:
    date: date | None = None
    provenance: str | None = None

    def to_dict(self) -> dict:
        return {"date": self.date.isoformat() if self.date else None, "provenance": self.provenance}


@dataclass(frozen=True)
class HarvestCampaign:
    """Harvests of a perennial separated by at most HARVEST_CAMPAIGN_GAP_DAYS (≤ 120 days)."""
    label: int
    first: date
    last: date
    harvests: tuple = ()

    @property
    def display(self) -> str:
        """Always "YYYY/YY": a perennial's campaign spans two years even before any January harvest."""
        return f"{self.label}/{(self.label + 1) % 100:02d}"

    def to_dict(self) -> dict:
        return {"label": self.label, "first": self.first.isoformat(), "last": self.last.isoformat(),
                "display": self.display, "harvests": [dict(h) for h in self.harvests]}


@dataclass(frozen=True)
class CropCycle:
    crop_id: str
    role: str
    species: str | None
    status: str | None
    lifecycle: str
    start: Boundary
    end: Boundary
    planned_start: date | None = None
    planned_end: date | None = None
    # Planned start already past, nothing sown or declared, not closed: the plan
    # is not the field. Never current, never previous; asks for the sowing.
    pending: bool = False
    # Harvest year declared on the AgriCrop; only used as the campaign when the
    # cycle has no dates.
    declared_campaign: int | None = None
    # Harvests of a perennial (refs, by date): a harvest does not close a perennial.
    harvests: tuple[dict, ...] = ()
    variety: str | None = None
    declared_yield_kg_ha: float | None = None
    harvested_weight_kg: float | None = None
    # Catalogue-shape fields (all defaulted: the legacy shape fills what its crop carries).
    kind: str = "crop"  # "crop" | "fallow" | "unidentified"
    crop: str | None = None  # URN of the catalogue AgriCrop
    crop_name: str | None = None
    irrigation_regime: str | None = None
    termination_method: str | None = None
    season: Any = None
    species_lifecycle: str | None = None  # "annual" | "perennial" | None, from the species table
    planned_sowing_end: date | None = None
    # Type of the operation that set the actual end ("harvesting", "tillage"); internal, not in to_dict.
    end_operation: str | None = field(default=None, compare=False, repr=False)

    @property
    def declared_only(self) -> bool:
        """A cycle known only by its declared campaign: no dates at all."""
        return (self.declared_campaign is not None
                and self.start.date is None and self.end.date is None)

    @property
    def campaign_label(self) -> int | None:
        """Harvest year (PAC). Without an end, estimated from the sowing: an annual
        sown from August on is harvested the next year (the August rule planning
        uses); a perennial keeps its planting year."""
        if self.end.date:
            return self.end.date.year
        if not self.start.date:
            return self.declared_campaign
        # A catch crop sown in late summer is normally ended the same autumn.
        if (self.lifecycle != _PERENNIAL and self.role != "catch_crop"
                and self.start.date.month >= _AUTUMN_SOWING_MONTH):
            return self.start.date.year + 1
        return self.start.date.year

    @property
    def harvest_campaigns(self) -> tuple[HarvestCampaign, ...]:
        groups: list[list[tuple[date, dict]]] = []
        for h in self.harvests:
            d = date.fromisoformat(h["date"])
            if groups and (d - groups[-1][-1][0]).days <= HARVEST_CAMPAIGN_GAP_DAYS:
                groups[-1].append((d, h))
            else:
                groups.append([(d, h)])
        return tuple(HarvestCampaign(label=g[0][0].year, first=g[0][0], last=g[-1][0],
                                     harvests=tuple(h for _, h in g)) for g in groups)

    def in_campaign(self, year: int) -> bool:
        """Perennial: alive in that year (planted by it, not uprooted before it).
        Any other cycle: its campaign label is that year."""
        if self.lifecycle == _PERENNIAL:
            s, e = self.start.date, self.end.date
            if s is None:  # declared only: matched by its declared campaign
                return self.campaign_label == year
            return bool(s) and s.year <= year and (e is None or e.year >= year)
        return self.campaign_label == year

    def to_dict(self) -> dict:
        out = {
            "crop_id": self.crop_id, "role": self.role, "species": self.species,
            "status": self.status, "lifecycle": self.lifecycle,
            "start": self.start.to_dict(), "end": self.end.to_dict(),
            "campaign_label": self.campaign_label, "pending": self.pending,
            "declared_campaign": self.declared_campaign,
            "planned_start": self.planned_start.isoformat() if self.planned_start else None,
            "planned_end": self.planned_end.isoformat() if self.planned_end else None,
            "variety": self.variety,
            "declared_yield_kg_ha": self.declared_yield_kg_ha,
            "harvested_weight_kg": self.harvested_weight_kg,
            "kind": self.kind, "crop": self.crop, "crop_name": self.crop_name,
            "irrigation_regime": self.irrigation_regime, "termination_method": self.termination_method,
            "season": self.season, "species_lifecycle": self.species_lifecycle,
            "planned_sowing_end": self.planned_sowing_end.isoformat() if self.planned_sowing_end else None,
        }
        out["harvests"] = [dict(h) for h in self.harvests]
        out["harvest_campaigns"] = [c.to_dict() for c in self.harvest_campaigns]
        return out


@dataclass(frozen=True)
class CropCycleTimeline:
    parcel_id: str
    at: date
    campaign_start: date
    campaign_end: date
    current: CropCycle | None
    previous: CropCycle | None
    next: CropCycle | None
    accumulation_start: date
    accumulation_basis: str
    cycles: tuple = ()
    unassigned: tuple = ()
    hints: tuple = ()
    pending: CropCycle | None = None

    def to_dict(self) -> dict:
        def c(x):
            return x.to_dict() if x else None

        return {
            "parcel_id": self.parcel_id, "at": self.at.isoformat(),
            "campaign": {"label": self.campaign_end.year, "start": self.campaign_start.isoformat(),
                         "end": self.campaign_end.isoformat()},
            "current": c(self.current), "previous": c(self.previous), "next": c(self.next),
            "pending": c(self.pending),
            "current_harvest_campaign": (
                self.current.harvest_campaigns[-1].to_dict()
                if self.current and self.current.harvest_campaigns else None),
            "accumulation": {"start": self.accumulation_start.isoformat(), "basis": self.accumulation_basis},
            "cycles": [cy.to_dict() for cy in sorted(self.cycles, key=lambda cy: cy.start.date or date.min)],
            "unassigned_operations": list(self.unassigned), "hints": list(self.hints),
        }


def _campaign_bounds(at: date, md: tuple[int, int]) -> tuple[date, date]:
    start = date(at.year, md[0], md[1])
    if at < start:
        start = date(at.year - 1, md[0], md[1])
    end = date(start.year + 1, md[0], md[1]) - timedelta(days=1)
    return start, end


def _belongs(entity: dict, parcel_id: str) -> bool:
    return _first(entity, "hasAgriParcel", "refAgriParcel") == parcel_id


def _op_ref(o: dict, d: date, source: str | None = None) -> dict:
    ref = {"id": o.get("id"), "operationType": _value(o.get("operationType")), "date": d.isoformat()}
    if source:
        ref["source"] = source
    return ref


def _window_contains(ps: date | None, pe: date | None, d: date) -> bool:
    if ps is None and pe is None:
        return False
    lo = (ps or pe) - timedelta(days=ASSIGN_TOLERANCE_DAYS)
    hi = (pe or ps) + timedelta(days=ASSIGN_TOLERANCE_DAYS)
    return lo <= d <= hi


def _perennial_window(start: date | None, pe: date | None, d: date, tolerance: int = ASSIGN_TOLERANCE_DAYS) -> bool:
    """A perennial lives from its planting on (no fixed season around it): an
    operation belongs to it from planting minus the tolerance until its planned
    end plus the tolerance, or without end limit when none is planned."""
    if start is None or d < start - timedelta(days=tolerance):
        return False
    return pe is None or d <= pe + timedelta(days=ASSIGN_TOLERANCE_DAYS)


def _distance(r: dict, d: date, is_end: bool) -> int:
    """Days between an operation and the planned boundary it would set.

    A sowing is matched to the nearest planned start, a harvest or termination
    to the nearest planned end: a harvest right before the next crop's sowing
    window belongs to the crop it ends, not to the one about to start.
    """
    if is_end:
        anchor = r["pe"] or r["ps"] or r["planting"]
    else:
        anchor = r["ps"] or r["planting"] or r["pe"]
    return abs((anchor - d).days)


def _sown_on(r: dict) -> date | None:
    """Date of a sowing that is a fact: a recorded operation, its copy on the crop, or a declared planting."""
    if r["starts"]:
        return max(d for d, _o in r["starts"])
    return _as_date(r["c"].get("actualPlantingDate")) or (r["planting"] if r["manual"] else None)


def _standing_annual(r: dict, d: date) -> bool:
    """An annual main crop sown by ``d`` and still in the field that day.

    Only an explicit ``annual``: a crop of unknown lifecycle may be a tree. Its own harvest days
    (within the tolerance after the first) belong to it too; their date, copied onto the crop as
    ``actualTerminationDate``, does not close it for the next day.
    """
    if r["lifecycle"] != _ANNUAL or r["role"] != "main_crop" or r["status"] in _CLOSED:
        return False
    sown = _sown_on(r)
    if sown is None or not sown <= d <= sown + timedelta(days=MAX_ANNUAL_STANDING_DAYS):
        return False
    ends = [e for e, _o in r["ends"] if e >= sown]
    if ends and d > min(ends) + timedelta(days=ASSIGN_TOLERANCE_DAYS):
        return False
    actual, declared = _as_date(r["c"].get("actualTerminationDate")), _as_date(r["c"].get("terminationDate"))
    if actual is not None and actual < d and actual not in ends:
        return False
    return declared is None or declared >= d


def _keeps_harvest(r: dict | None, d: date, target: Any) -> bool:
    """Whether the cycle chosen by its window keeps the harvest: linked to it, a stand, or a main crop sown by then."""
    if r is None:
        return False
    if target and r["c"].get("id") == target:
        return True
    sown = _sown_on(r)
    if sown is not None and sown <= d:
        return r["lifecycle"] == _PERENNIAL or r["role"] == "main_crop"
    # A stand without a recorded sowing is in the field by its status; a plan is not.
    return r["lifecycle"] == _PERENNIAL and r["status"] != "planned"


def _operation_order(o: dict) -> tuple:
    """Harvests last, by date and id; the rest in the order they arrive."""
    if _value(o.get("operationType")) != "harvesting":
        return (False, date.min, "")
    return (True, _as_date(o.get("endedAt")) or _as_date(o.get("startedAt")) or date.min, str(o.get("id") or ""))


def _current_rank(cy: CropCycle) -> tuple:
    """Order among overlapping candidates: a cycle whose start is a fact (sown or
    declared) beats a planned one, and the most recent fact wins — a field sown
    in August supersedes an older crop whose planned harvest passed unreported.
    Among planned starts, an active crop first, then the latest start."""
    fact = cy.start.provenance in ("actual", "declared")
    return (fact, cy.start.date if fact else date.min, cy.status == "active", cy.start.date)


def resolve_crop_cycles(
    parcel_id: str,
    crops: Iterable[dict],
    operations: Iterable[dict],
    at: date,
    campaign_start: tuple[int, int] = (1, 1),
) -> CropCycleTimeline:
    raw = []
    for c in crops:
        if not _belongs(c, parcel_id):
            continue
        status = _value(c.get("status"))
        if _value(c.get("provenance")) == "placeholder" or status in _EXCLUDED:
            continue
        manual = _value(c.get("plantingDateSource")) == "manual"
        planting = _as_date(c.get("plantingDate"))
        ps = None if manual else (planting or _as_date(c.get("sowingWindowStart")))
        lc = _value(c.get("cropLifecycle"))
        lifecycle = lc if lc in (_ANNUAL, _PERENNIAL) else "unknown"
        # Catalogue shape: an unknown lifecycle of a perennial species is assigned as a stand (a harvest
        # does not close it, only uprooting does) while the cycle keeps reporting "unknown".
        out_lifecycle = lifecycle
        if lifecycle == "unknown" and c.get(_PERENNIAL_SPECIES_KEY):
            lifecycle = _PERENNIAL
        # A perennial's harvestDate is the next harvest, not the end of the stand.
        pe = (_as_date(c.get("expectedTerminationDate")) if lifecycle == _PERENNIAL
              else _as_date(c.get("harvestDate")) or _as_date(c.get("expectedTerminationDate")))
        raw.append({"c": c, "status": status, "manual": manual, "planting": planting, "ps": ps, "pe": pe,
                    "role": _first(c, "cropRole", "role") or "main_crop", "lifecycle": lifecycle,
                    "out_lifecycle": out_lifecycle,
                    "starts": [], "ends": [], "harvests": []})

    unassigned, hints = [], []
    # Harvests are resolved last: whether a stand was already uprooted must not
    # depend on the order the operations arrive in.
    for o in sorted(operations, key=_operation_order):
        if not _belongs(o, parcel_id):
            continue
        d = _as_date(o.get("endedAt")) or _as_date(o.get("startedAt"))
        if d is None:
            continue
        status = _value(o.get("status"))
        if status == "suggested":
            hints.append(_op_ref(o, d, "suggested"))
            continue
        if status not in _DONE:
            continue
        typ = _value(o.get("operationType"))
        is_start = typ == "sowing"
        tillage_type = _value(o.get("tillageType")) if typ == "tillage" else None
        is_uprooting = tillage_type == UPROOTING
        is_harvest = typ == "harvesting"
        is_end = is_harvest or tillage_type in _TERMINATING_TILLAGE or is_uprooting
        if not (is_start or is_end):
            continue

        def in_window(r, when=d):
            if r["lifecycle"] == _PERENNIAL and (is_harvest or is_uprooting):
                # A stand uprooted before this harvest has no harvest after it.
                if is_harvest and r["ends"] and when > min(e for e, _o in r["ends"]):
                    return False
                # A harvest never precedes its stand's planting: no negative tolerance.
                return _perennial_window(r["ps"] or r["planting"], r["pe"], when,
                                         0 if is_harvest else ASSIGN_TOLERANCE_DAYS)
            return _window_contains(r["ps"] or r["planting"], r["pe"], when)

        linked = _value(o.get(_LINKED_CYCLE_KEY))

        def linked_ok(r, when=d):
            # An explicit link to a cycle needs no window; a stand uprooted before still takes no harvest.
            if not linked or r["c"].get("id") != linked:
                return False
            return not (is_harvest and r["lifecycle"] == _PERENNIAL and r["ends"]
                        and when > min(e for e, _o in r["ends"]))

        cands = [r for r in raw if in_window(r) or linked_ok(r)]
        if is_uprooting:
            # Only uprooting closes a perennial; it never closes an annual or a cover.
            cands = [r for r in cands if r["lifecycle"] == _PERENNIAL]
        elif is_end and typ == "tillage":
            # Rolling or mowing ends a cover or catch crop; a main crop or a perennial stand is mowed and lives on.
            cands = [r for r in cands if r["role"] in _TERMINABLE_ROLES]
        target = _first(o, "hasAgriCrop", "refAgriCrop")
        best = next((r for r in cands if target and r["c"].get("id") == target), None)
        if best is None and (is_harvest or is_uprooting):
            # Only stands alive that day: planted by then and not uprooted before.
            stands = [r for r in cands if r["lifecycle"] == _PERENNIAL and r["role"] == "main_crop"
                      and (r["ps"] or r["planting"]) is not None and (r["ps"] or r["planting"]) <= d
                      and not (r["ends"] and d > min(e for e, _o in r["ends"]))]
            if stands:  # a harvest under a cover crop is the olive's, not the cover's
                best = max(stands, key=lambda r: r["ps"] or r["planting"])
        if best is None and cands:
            best = min(cands, key=lambda r, when=d, end=is_end: _distance(r, when, end))
        if is_harvest and not _keeps_harvest(best, d, target):
            # A plan stores no end for an annual, so its harvest falls outside every window: it is the
            # crop standing that day that is harvested, never a plan not sown yet or the cover on top.
            standing = [r for r in raw if _standing_annual(r, d)]
            if standing:
                best = next((r for r in standing if target and r["c"].get("id") == target), None) \
                    or max(standing, key=lambda r: (_sown_on(r), str(r["c"].get("id") or "")))
        if best is None:
            unassigned.append(_op_ref(o, d))
            continue
        if is_start:
            best["starts"].append((d, o))
        elif is_harvest and best["lifecycle"] == _PERENNIAL:
            best["harvests"].append((d, o))
        else:
            best["ends"].append((d, o))

    cycles = []
    for r in raw:
        c = r["c"]
        if r["starts"]:
            start = Boundary(max(d for d, _o in r["starts"]), "actual")
        elif _as_date(c.get("actualPlantingDate")):
            start = Boundary(_as_date(c.get("actualPlantingDate")), "actual")
        elif r["manual"] and r["planting"]:
            start = Boundary(r["planting"], "declared")
        elif r["ps"]:
            start = Boundary(r["ps"], "planned")
        else:
            start = Boundary()
        harvest_weights: list[float] = []
        ends = []
        end_ops = []
        for e, o in r["ends"]:
            if start.date is None or e >= start.date:
                ends.append(e)
                end_ops.append((e, 0 if _value(o.get("operationType")) == "harvesting" else 1,
                                _value(o.get("operationType"))))
                hw = _op_harvested_weight(o)
                if hw is not None:
                    harvest_weights.append(hw)
            else:  # an end before the cycle began cannot be its end: report it
                unassigned.append(_op_ref(o, e))
        if ends:
            end = Boundary(min(ends), "actual")
        elif _as_date(c.get("actualTerminationDate")):
            end = Boundary(_as_date(c.get("actualTerminationDate")), "actual")
        elif _as_date(c.get("terminationDate")):
            end = Boundary(_as_date(c.get("terminationDate")), "declared")
        elif r["pe"]:
            end = Boundary(r["pe"], "planned")
        else:
            end = Boundary()
        harvests = []
        for h, o in r["harvests"]:
            if start.date is None or h >= start.date:
                harvests.append(_op_ref(o, h))
                hw = _op_harvested_weight(o)
                if hw is not None:
                    harvest_weights.append(hw)
            else:  # a harvest before the planting cannot be this stand's
                unassigned.append(_op_ref(o, h))
        harvested_weight_kg = round(sum(harvest_weights), 2) if harvest_weights else None
        species = _value(c.get("species"))
        cycles.append(CropCycle(
            crop_id=c.get("id"), role=r["role"], species=species, status=r["status"],
            lifecycle=r["out_lifecycle"],
            start=start, end=end, planned_start=r["ps"], planned_end=r["pe"],
            declared_campaign=_declared_campaign(c),
            harvests=tuple(sorted(harvests, key=lambda h: (h["date"], h["id"] or ""))),
            variety=_variety(c),
            declared_yield_kg_ha=_declared_yield(c),
            harvested_weight_kg=harvested_weight_kg,
            irrigation_regime=_first(c, "irrigationRegime"),
            termination_method=_first(c, "terminationMethod"),
            season=_first(c, "cropSeason"),
            species_lifecycle=species_lifecycle(species) if isinstance(species, str) else None,
            planned_sowing_end=None if r["manual"] else _as_date(c.get("sowingWindowEnd")),
            end_operation=min(end_ops)[2] if ends and end.provenance == "actual" and end_ops
            and end.date == min(ends) else None,
        ))

    def is_pending(cy: CropCycle) -> bool:
        return (cy.start.provenance == "planned" and cy.start.date is not None
                and cy.start.date <= at and cy.status not in _CLOSED)

    cycles = [replace(cy, pending=True) if is_pending(cy) else cy for cy in cycles]

    def is_current(cy: CropCycle) -> bool:
        # Only a sowing that happened or was declared puts a crop in the field;
        # a plan whose date has passed is pending, not current.
        if cy.start.provenance not in _FACTS:
            return False
        if cy.start.date is None or cy.start.date > at or cy.status in _CLOSED:
            return False
        if cy.end.date is None or at <= cy.end.date:
            return True
        # Planned end already passed but the field has not reported it: still growing.
        return cy.end.provenance == "planned" and cy.status == "active"

    current_c = [cy for cy in cycles if is_current(cy)]
    current = max(current_c, key=_current_rank) if current_c else None
    past = [cy for cy in cycles if cy is not current and not cy.pending
            and cy.start.date and cy.start.date <= at
            and (cy.status in _CLOSED or (cy.end.date and cy.end.date < at))]
    # Dated cycles resolve exactly as before declared campaigns existed.
    dated_prev = max(past, key=lambda cy: cy.end.date or cy.start.date) if past else None
    # A declared-only cycle is history once closed and its campaign is over.
    # Same-campaign ties keep the first listed. A dated cycle with its own
    # declaredCampaign keeps its dates label; a declared-only cycle that is not
    # closed is never past.
    declared_past = [cy for cy in cycles if cy.declared_only and cy.status in _CLOSED
                     and (cy.declared_campaign < at.year
                          or (cy.declared_campaign == at.year and at.month >= DECLARED_ROLLOVER_MONTH))]
    declared_prev = max(declared_past, key=lambda cy: cy.declared_campaign) if declared_past else None
    # Facts beat declarations: a declared campaign wins only if strictly later.
    if declared_prev and (dated_prev is None or declared_prev.campaign_label > dated_prev.campaign_label):
        previous = declared_prev
    else:
        previous = dated_prev
    future = [cy for cy in cycles if cy.start.date and cy.start.date > at]
    nxt = min(future, key=lambda cy: cy.start.date) if future else None
    pending_c = [cy for cy in cycles if cy.pending]
    pending = max(pending_c, key=lambda cy: cy.start.date) if pending_c else None

    c_start, c_end = _campaign_bounds(at, campaign_start)
    if current is None:
        acc, basis = c_start, "calendar_year"
    elif current.lifecycle == _PERENNIAL:
        acc, basis = max(c_start, current.start.date), "campaign_start"
    else:
        acc, basis = current.start.date, "cycle_start"

    return CropCycleTimeline(
        parcel_id=parcel_id, at=at, campaign_start=c_start, campaign_end=c_end,
        current=current, previous=previous, next=nxt,
        accumulation_start=acc, accumulation_basis=basis,
        cycles=tuple(cycles), unassigned=tuple(unassigned), hints=tuple(hints),
        pending=pending,
    )


# ── Catalogue shape: the cycle lives in the operations ───────────────────────
#
# AgriCrop is a generic catalogue entity (no parcel link); a sowing operation opens the cycle
# and the plan is the same operation in status ``planned``. ``resolve_parcel_crops`` is an
# adapter: it turns that data into the crop/operation dicts ``resolve_crop_cycles`` already
# understands, runs it, then post-processes the result. A parcel without the new shape goes
# through ``resolve_crop_cycles`` untouched.

_NKZ_NS = "https://nkz-os.org/ns/"
_PLANNED = "planned"
_CAMPAIGN_RANGE = (1900, 2200)
_DECLARED_ID = "{parcel}#declared-{campaign}"
# A fallow is valid from 1 August of the previous year to 31 July of its campaign.
_FALLOW_WINDOW_START = (8, 1)
_FALLOW_WINDOW_END = (7, 31)


def _read(entity: dict, *names: str) -> Any:
    """First non-empty value among ``names``, each as short name, ``nkz:`` term or full IRI."""
    for n in names:
        for key in (n, f"nkz:{n}", f"{_NKZ_NS}{n}"):
            v = _value(entity.get(key))
            if v not in (None, ""):
                return v
    return None


def _str_read(entity: dict, *names: str) -> str | None:
    v = _read(entity, *names)
    return v if isinstance(v, str) else None


def _crop_target(o: dict) -> str | None:
    return _str_read(o, "hasAgriCrop", "refAgriCrop")


def _is_agricrop(entity: dict) -> bool:
    t = _value(entity.get("type"))
    return isinstance(t, str) and (t == "AgriCrop" or t.endswith("/AgriCrop"))


def _is_catalogue(crop: dict) -> bool:
    """A catalogue crop is an AgriCrop with no parcel link."""
    return _is_agricrop(crop) and _read(crop, "hasAgriParcel", "refAgriParcel") is None


@dataclass(frozen=True)
class ParcelCropData:
    """What a reader needs to resolve a parcel's cycles, in either shape."""
    parcel: dict
    operations: tuple[dict, ...]
    legacy_crops: tuple[dict, ...]
    crops: Mapping[str, dict] = field(default_factory=dict)  # every crop read by URN (catalogue or not)
    products: Mapping[str, dict] = field(default_factory=dict)  # AgriProductType by URN; absent = not found


def _catalogue_crop(data: ParcelCropData, urn: str | None) -> dict | None:
    crop = data.crops.get(urn) if urn else None
    return crop if crop is not None and _is_catalogue(crop) else None


def declared_campaign_entries(parcel: dict) -> list[dict]:
    """``AgriParcel.declaredCampaigns`` as a list of dicts (Orion returns one entry as a bare object)."""
    raw = _read(parcel or {}, "declaredCampaigns")
    items = raw if isinstance(raw, list) else [raw]
    return [it for it in items if isinstance(it, dict)]


def has_catalogue_shape(data: ParcelCropData) -> bool:
    """True when an operation points to a catalogue crop or the parcel declares campaigns."""
    if declared_campaign_entries(data.parcel):
        return True
    return any(_catalogue_crop(data, _crop_target(o)) is not None for o in data.operations)


def _entry_crop(entry: dict) -> str | None:
    v = _value(entry.get("crop"))
    return v if isinstance(v, str) and v else None


def _without_crop(o: dict) -> dict:
    return {k: v for k, v in o.items() if k not in ("hasAgriCrop", "refAgriCrop")}


def _entry_campaign(entry: dict) -> int | None:
    v = _value(entry.get("campaign"))
    ok = isinstance(v, int) and not isinstance(v, bool) and _CAMPAIGN_RANGE[0] <= v <= _CAMPAIGN_RANGE[1]
    return v if ok else None


def _parcel_id_of(data: ParcelCropData) -> str:
    pid = _value((data.parcel or {}).get("id"))
    if isinstance(pid, str) and pid:
        return pid
    for e in (*data.operations, *data.legacy_crops):
        link = _read(e, "hasAgriParcel", "refAgriParcel")
        if isinstance(link, str):
            return link
    return ""


def _plan_day(o: dict) -> date | None:
    return _as_date(_read(o, "plannedStartAt"))


def _op_day(o: dict) -> date | None:
    return _as_date(o.get("endedAt")) or _as_date(o.get("startedAt"))


@dataclass(frozen=True)
class _Sowing:
    op: dict
    id: str
    crop_urn: str
    crop: dict
    role: str
    day: date | None
    manual: bool


def _crop_identity(crop: dict) -> tuple[str | None, str | None]:
    """(EPPO code or None, English name or None)."""
    name = _value(crop.get("name"))
    name = name.strip() if isinstance(name, str) and name.strip() else None
    return eppo_of(crop), name


def _pick(ops: list[dict], name: str) -> Any:
    for o in ops:
        v = _read(o, name)
        if v is not None:
            return v
    return None


def _variety_name(data: ParcelCropData, ops: list[dict]) -> str | None:
    for o in ops:
        urn = _str_read(o, "hasAgriProductType")
        if not urn:
            continue
        product = data.products.get(urn)
        if product is None:
            logger.warning("crop catalogue: variety %s not found, cycle without variety", urn)
            continue
        name = _value(product.get("name"))
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _end_kind(o: dict) -> str | None:
    typ = _value(o.get("operationType"))
    if typ == "harvesting":
        return "harvest"
    if typ == "tillage":
        tillage = _value(o.get("tillageType"))
        if tillage == UPROOTING:
            return "uproot"
        if tillage in _TERMINATING_TILLAGE:
            return "terminate"
    return None


def resolve_parcel_crops(data: ParcelCropData, at: date, campaign_start: tuple[int, int] = (1, 1)) -> CropCycleTimeline:
    """The parcel's cycle timeline from either shape (see module notes)."""
    parcel_id = _parcel_id_of(data)
    if not has_catalogue_shape(data):
        return resolve_crop_cycles(parcel_id, data.legacy_crops, data.operations, at, campaign_start)
    return _resolve_catalogue(data, parcel_id, at, campaign_start)


def _resolve_catalogue(data: ParcelCropData, parcel_id: str, at: date, campaign_start: tuple[int, int]) -> CropCycleTimeline:
    # Rule 1: with the catalogue shape the legacy AgriCrop of this parcel are ignored entirely.
    sowings: list[_Sowing] = []
    rest: list[dict] = []
    for o in data.operations:
        crop_urn = _crop_target(o)
        crop = _catalogue_crop(data, crop_urn)
        status = _value(o.get("status"))
        if (crop is not None and _value(o.get("operationType")) == "sowing" and _belongs(o, parcel_id)
                and (status == _PLANNED or status in _DONE)):
            day = _op_day(o) if status in _DONE else _plan_day(o)
            if day is None and status in _DONE:
                # Never skipped: a sowing that happened but has no date would silently drop a cycle.
                raise CropDataUnavailable(f"completed sowing without a date: {o.get('id')}")
            sowings.append(_Sowing(
                op=o, id=str(o.get("id")), crop_urn=crop_urn, crop=crop,
                role=_read(o, "cropRole") or "main_crop", day=day,
                manual=status in _DONE and _read(o, "plantingDateSource") == "manual"))
        else:
            rest.append(o)
    done = sorted((s for s in sowings if _value(s.op.get("status")) in _DONE), key=lambda s: (s.day, s.id))
    plans = sorted((s for s in sowings if _value(s.op.get("status")) == _PLANNED),
                   key=lambda s: (s.day or date.max, s.id))

    end_ops = []
    for o in rest:
        kind = _end_kind(o)
        day = _op_day(o)
        if kind and day and _value(o.get("status")) in _DONE:
            end_ops.append((day, kind, _crop_target(o), _str_read(o, "hasSowingOperation")))

    def lifecycle_hint(s: _Sowing) -> str | None:
        lc = _read(s.op, "cropLifecycle")
        return lc if lc in (_ANNUAL, _PERENNIAL) else species_lifecycle(eppo_of(s.crop))

    def ended_between(lo: date, hi: date, group: dict) -> bool:
        crop_urn, role = group["key"]
        ids = {m.id for m in group["members"]}
        for day, kind, target, sowing_id in end_ops:
            if not lo <= day <= hi or target not in (None, crop_urn):
                continue
            if sowing_id is not None and sowing_id not in ids:
                continue
            if (kind == "harvest" and group["perennial"]) or (kind == "terminate" and role not in _TERMINABLE_ROLES):
                continue
            return True
        return False

    # Rules 3 and 4: same crop and role, within 60 days of the FIRST, nothing ended it in between.
    groups: list[dict] = []
    last: dict[tuple, dict] = {}
    for s in done:
        key = (s.crop_urn, s.role)
        g = last.get(key)
        if g is not None:
            first = g["members"][0]
            if (s.day - first.day).days <= ASSIGN_TOLERANCE_DAYS and not ended_between(first.day, s.day, g):
                g["members"].append(s)
                continue
        g = {"key": key, "members": [s], "perennial": lifecycle_hint(s) == _PERENNIAL, "plan": None}
        groups.append(g)
        last[key] = g

    for g in groups:
        # The latest member decides the provenance, as the resolver does for its dates.
        g["manual"] = g["members"][-1].manual

    # A sowing that falls in the window of a plan of the same crop completes that plan.
    used: set[str] = set()
    for g in groups:
        crop_urn, role = g["key"]
        first = g["members"][0]
        if g["manual"] or any(_read(m.op, "cropSeason") is not None for m in g["members"]):
            continue  # a declared planting ignores plans; a completed plan carries its own
        best, best_key = None, None
        for p in plans:
            if p.id in used or (p.crop_urn, p.role) != (crop_urn, role):
                continue
            pe = _as_date(_read(p.op, "expectedTerminationDate"))
            if not _window_contains(p.day, pe, first.day):
                continue
            k = (abs(((p.day or pe) - first.day).days), p.id)
            if best_key is None or k < best_key:
                best, best_key = p, k
        if best is not None:
            g["plan"] = best
            used.add(best.id)

    crops_in: list[dict] = []
    meta: dict[str, dict] = {}
    ops_out: list[dict] = []
    op_cycle: dict[str, str] = {}

    def synthetic(cid: str, s: _Sowing, pri: list[dict], status: str) -> dict:
        eppo, name = _crop_identity(s.crop)
        d: dict[str, Any] = {"id": cid, "hasAgriParcel": parcel_id, "status": status, "cropRole": s.role,
                             "species": eppo or name, "variety": _variety_name(data, pri)}
        for attr in ("cropLifecycle", "irrigationRegime", "terminationMethod",
                     "expectedTerminationDate", "cropSeason"):
            v = _pick(pri, attr)
            if v is not None:
                d[attr] = v
        mgmt = _pick(pri, "cropManagement")
        if mgmt is not None:
            d["management"] = mgmt
        if d.get("cropLifecycle") not in (_ANNUAL, _PERENNIAL) and species_lifecycle(eppo) == _PERENNIAL:
            d[_PERENNIAL_SPECIES_KEY] = True
        meta[cid] = {"kind": "fallow" if is_fallow(s.crop) else "crop", "crop": s.crop_urn,
                     "name": name, "eppo": eppo, "fake_ps": False, "declared": False,
                     "species_lifecycle": species_lifecycle(eppo)}
        crops_in.append(d)
        return d

    for g in groups:
        members, plan = g["members"], g["plan"]
        s0 = members[0]
        cid = plan.id if plan else s0.id
        pri = ([plan.op] if plan else []) + [m.op for m in reversed(members)]
        d = synthetic(cid, s0, pri, "active")
        for m in members:
            op_cycle[m.id] = cid
        if plan:
            op_cycle[plan.id] = cid
        if g["manual"]:  # declared planting: its date is the start, no sowing operation is passed
            d["plantingDateSource"] = "manual"
            d["plantingDate"] = max(m.day for m in members).isoformat()
            continue
        if plan:
            ps, sw_end = plan.day, _as_date(_read(plan.op, "plannedEndAt"))
        else:
            own = next((m for m in members if _read(m.op, "cropSeason") is not None), None)
            ps = _plan_day(own.op) if own else None
            sw_end = _as_date(_read(own.op, "plannedEndAt")) if own and ps else None
            if ps is None:
                # No window to match the sowings against: borrow the first sowing's day, hide it afterwards.
                ps, meta[cid]["fake_ps"] = s0.day, True
        if ps is not None:
            d["sowingWindowStart"] = ps.isoformat()
        if sw_end is not None:
            d["sowingWindowEnd"] = sw_end.isoformat()
        for m in members:
            if not m.manual:
                ops_out.append({**m.op, "hasAgriCrop": cid, _LINKED_CYCLE_KEY: cid})

    for p in plans:
        if p.id in used:
            continue
        d = synthetic(p.id, p, [p.op], "planned")
        op_cycle[p.id] = p.id
        if p.day is not None:
            d["sowingWindowStart"] = p.day.isoformat()
        sw_end = _as_date(_read(p.op, "plannedEndAt"))
        if sw_end is not None:
            d["sowingWindowEnd"] = sw_end.isoformat()

    for o in rest:
        sid = _str_read(o, "hasSowingOperation")
        cid = op_cycle.get(sid) if sid else None
        ops_out.append({**o, "hasAgriCrop": cid, _LINKED_CYCLE_KEY: cid} if cid else o)

    seen_campaigns: dict[int, int] = {}
    for entry in declared_campaign_entries(data.parcel):
        campaign = _entry_campaign(entry)
        if campaign is None:
            raise CropDataUnavailable(f"declared campaign malformed (integer {_CAMPAIGN_RANGE[0]}-{_CAMPAIGN_RANGE[1]}"
                                      f" expected): {parcel_id}")
        n = seen_campaigns[campaign] = seen_campaigns.get(campaign, 0) + 1
        cid = _DECLARED_ID.format(parcel=parcel_id, campaign=campaign) + ("" if n == 1 else f"-{n}")
        crop_urn = _entry_crop(entry)
        crop = data.crops.get(crop_urn) if crop_urn else None
        eppo, name = _crop_identity(crop) if crop else (None, None)
        fallow = _value(entry.get("kind")) == "fallow" or (crop is not None and is_fallow(crop))
        status = "harvested"
        if fallow:
            # Not closed until its window ends: the resolver would call it past from July.
            status = ("terminated" if at > date(campaign, *_FALLOW_WINDOW_END) else "planned")
        d = {"id": cid, "hasAgriParcel": parcel_id, "status": status, "cropRole": "main_crop",
             "species": eppo or name, "nkz:declaredCampaign": campaign}
        variety = _value(entry.get("variety"))
        if isinstance(variety, str) and variety.strip():
            d["variety"] = variety.strip()
        yield_kg = _value(entry.get("yieldKgHa"))
        if yield_kg is not None:
            d["declaredYieldKgHa"] = yield_kg
        crops_in.append(d)
        meta[cid] = {"kind": "fallow" if fallow else "crop", "crop": crop_urn, "name": name, "eppo": eppo,
                     "fake_ps": False, "declared": True, "species_lifecycle": species_lifecycle(eppo)}

    timeline = resolve_crop_cycles(parcel_id, crops_in, ops_out, at, campaign_start)

    def fix(cy: CropCycle) -> CropCycle:
        m = meta.get(cy.crop_id)
        if m is None:
            return cy
        status = cy.status
        if cy.end_operation == "harvesting":
            status = "harvested"
        elif cy.end_operation == "tillage":
            status = "terminated"
        elif not m["declared"] and status == "planned" and cy.start.provenance in _FACTS:
            status = "active"
        return replace(cy, status=status, kind=m["kind"], crop=m["crop"], crop_name=m["name"],
                       species_lifecycle=m["species_lifecycle"],
                       planned_start=None if m["fake_ps"] else cy.planned_start)

    by_id = {cy.crop_id: fix(cy) for cy in timeline.cycles}
    current = timeline.current
    if current is not None and current.crop_id in meta:
        m = meta[current.crop_id]
        if m["kind"] == "crop" and current.role == "main_crop" and not m["eppo"]:
            # A current main crop the writers should have refused: no species, flagged, never guessed.
            by_id[current.crop_id] = replace(by_id[current.crop_id], kind="unidentified", species=None)
    if current is None:
        for cy in list(by_id.values()):
            m = meta.get(cy.crop_id)
            if (m and m["declared"] and m["kind"] == "fallow" and cy.declared_campaign is not None
                    and date(cy.declared_campaign - 1, *_FALLOW_WINDOW_START) <= at
                    <= date(cy.declared_campaign, *_FALLOW_WINDOW_END)):
                by_id[cy.crop_id] = replace(cy, status="active")
                current = cy
                break

    def pick(cy: CropCycle | None) -> CropCycle | None:
        return by_id.get(cy.crop_id, cy) if cy is not None else None

    return replace(timeline, cycles=tuple(by_id.values()), current=pick(current), previous=pick(timeline.previous),
                   next=pick(timeline.next), pending=pick(timeline.pending))
