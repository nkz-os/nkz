"""Crop cycles and campaigns — the single resolution of a parcel's crop timeline.

A crop cycle is one AgriCrop (main, cover or catch crop) from sowing to harvest or
termination. Every boundary carries its provenance: what was done in the field
(``actual``) beats what a person declared (``declared``), which beats a plan
(``planned``). The campaign is the calendar year; a cycle belongs to the
campaign of the year it ends in. Pure function: no I/O, deterministic.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

ASSIGN_TOLERANCE_DAYS = 60
_CLOSED = {"harvested", "terminated"}
_EXCLUDED = {"cancelled"}
_PERENNIAL, _ANNUAL = "perennial", "annual"


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


def _first(entity: dict, *names: str) -> Any:
    for n in names:
        v = _value(entity.get(n))
        if v not in (None, ""):
            return v
    return None


@dataclass(frozen=True)
class Boundary:
    date: date | None = None
    provenance: str | None = None

    def to_dict(self) -> dict:
        return {"date": self.date.isoformat() if self.date else None, "provenance": self.provenance}


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

    @property
    def campaign_label(self) -> int | None:
        if self.end.date:
            return self.end.date.year
        return self.start.date.year if self.start.date else None

    def to_dict(self) -> dict:
        return {
            "crop_id": self.crop_id, "role": self.role, "species": self.species,
            "status": self.status, "lifecycle": self.lifecycle,
            "start": self.start.to_dict(), "end": self.end.to_dict(),
            "campaign_label": self.campaign_label,
        }


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

    def to_dict(self) -> dict:
        def c(x):
            return x.to_dict() if x else None

        return {
            "parcel_id": self.parcel_id, "at": self.at.isoformat(),
            "campaign": {"label": self.campaign_end.year, "start": self.campaign_start.isoformat(),
                         "end": self.campaign_end.isoformat()},
            "current": c(self.current), "previous": c(self.previous), "next": c(self.next),
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


def _distance(r: dict, d: date) -> int:
    """Days between an operation and a cycle's planned anchor (start, else end)."""
    return abs(((r["ps"] or r["planting"] or r["pe"]) - d).days)


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
        pe = _as_date(c.get("harvestDate")) or _as_date(c.get("expectedTerminationDate"))
        raw.append({"c": c, "status": status, "manual": manual, "planting": planting, "ps": ps, "pe": pe,
                    "role": _first(c, "cropRole", "role") or "main_crop",
                    "starts": [], "ends": []})

    unassigned, hints = [], []
    for o in operations:
        if not _belongs(o, parcel_id):
            continue
        d = _as_date(o.get("endedAt")) or _as_date(o.get("startedAt"))
        if d is None:
            continue
        status = _value(o.get("status"))
        if status == "suggested":
            hints.append(_op_ref(o, d, "suggested"))
            continue
        if status != "completed":
            continue
        typ = _value(o.get("operationType"))
        is_start = typ == "sowing"
        is_end = typ == "harvesting" or (typ == "tillage" and _value(o.get("tillageType")) == "roller_crimper")
        if not (is_start or is_end):
            continue
        cands = [r for r in raw if _window_contains(r["ps"] or r["planting"], r["pe"], d)]
        if is_end and typ == "tillage":
            cands = [r for r in cands if r["role"] == "cover_crop"]
        if not cands:
            unassigned.append(_op_ref(o, d))
            continue
        best = min(cands, key=lambda r, when=d: _distance(r, when))
        (best["starts"] if is_start else best["ends"]).append(d)

    cycles = []
    for r in raw:
        c = r["c"]
        if r["starts"]:
            start = Boundary(max(r["starts"]), "actual")
        elif _as_date(c.get("actualPlantingDate")):
            start = Boundary(_as_date(c.get("actualPlantingDate")), "actual")
        elif r["manual"] and r["planting"]:
            start = Boundary(r["planting"], "declared")
        elif r["ps"]:
            start = Boundary(r["ps"], "planned")
        else:
            start = Boundary()
        ends = [e for e in r["ends"] if start.date is None or e >= start.date]
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
        lc = _value(c.get("cropLifecycle"))
        cycles.append(CropCycle(
            crop_id=c.get("id"), role=r["role"], species=_value(c.get("species")), status=r["status"],
            lifecycle=lc if lc in (_ANNUAL, _PERENNIAL) else "unknown",
            start=start, end=end, planned_start=r["ps"], planned_end=r["pe"],
        ))

    def is_current(cy: CropCycle) -> bool:
        if cy.start.date is None or cy.start.date > at or cy.status in _CLOSED:
            return False
        if cy.end.date is None or at <= cy.end.date:
            return True
        # Planned end already passed but the field has not reported it: still growing.
        return cy.end.provenance == "planned" and cy.status == "active"

    current_c = [cy for cy in cycles if is_current(cy)]
    current = max(current_c, key=lambda cy: (cy.status == "active", cy.start.date)) if current_c else None
    past = [cy for cy in cycles if cy is not current and cy.start.date and cy.start.date <= at
            and (cy.status in _CLOSED or (cy.end.date and cy.end.date < at))]
    previous = max(past, key=lambda cy: cy.end.date or cy.start.date) if past else None
    future = [cy for cy in cycles if cy.start.date and cy.start.date > at]
    nxt = min(future, key=lambda cy: cy.start.date) if future else None

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
    )
