"""
observations.py — client for the timeseries-reader observation endpoints.

Endpoints (services/timeseries-reader/observations/routes.py):
    POST /api/observations/best        best observation per bucket (tiered, never fused across groups)
    POST /api/observations             raw stored observations, cursor-paged (CSV is not exposed here)
    GET  /api/observations/properties  the observable-property catalogue

Auth is the module -> core HMAC: every request carries ``X-Tenant-ID`` and
``X-Auth-Signature = generate_hmac_signature(secret, "", tenant_id)``.

All datetimes are timezone-aware: naive datetimes are rejected (ValueError), outgoing
values are ISO-8601 UTC with a trailing ``Z``, incoming values are parsed to aware datetimes.
Wire names are camelCase, dataclass fields snake_case. Errors raise ``ObservationsError``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Iterator
from urllib.parse import urljoin

import httpx

from nkz_platform_sdk.crypto import generate_hmac_signature

DEFAULT_BASE_URL = "http://timeseries-reader-service:5000"
DEFAULT_TIMEOUT = 30.0
# The server validates from < to even for mode "latest" (it then substitutes the
# property's max_age window). The client sends this nominal window.
_LATEST_NOMINAL_WINDOW = timedelta(hours=1)


class ObservationsError(Exception):
    """The reader answered with a non-2xx status."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status}: {message}")
        self.status = status
        self.message = message


# ------------------------------------------------------------------ types

def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"server returned a naive datetime: {value!r}")
    return dt


@dataclass(frozen=True)
class ObservationSource:
    kind: str
    id: str
    deployment_id: str | None = None
    distance_m: float | None = None
    n: int | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "ObservationSource":
        return cls(kind=d["kind"], id=d["id"], deployment_id=d.get("deploymentId"),
                   distance_m=d.get("distanceM"), n=d.get("n"))


@dataclass(frozen=True)
class ObservationPoint:
    t: datetime
    value: float | None
    tier: int | None
    fidelity: str
    confidence: str
    calibrated: bool
    medium_fallback: bool
    coverage: float
    sources: tuple[ObservationSource, ...]

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "ObservationPoint":
        return cls(
            t=_dt(d["t"]), value=d.get("value"), tier=d.get("tier"), fidelity=d["fidelity"],
            confidence=d["confidence"], calibrated=bool(d.get("calibrated")),
            medium_fallback=bool(d.get("mediumFallback")), coverage=d.get("coverage", 0.0),
            sources=tuple(ObservationSource.from_json(s) for s in d.get("sources") or ()),
        )


@dataclass(frozen=True)
class ObservationGroup:
    sub_context: dict[str, Any]
    points: tuple[ObservationPoint, ...]

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "ObservationGroup":
        return cls(sub_context=dict(d.get("subContext") or {}),
                   points=tuple(ObservationPoint.from_json(p) for p in d.get("points") or ()))


@dataclass(frozen=True)
class BestResult:
    feature: str | None
    property: str
    unit_code: str | None
    unit_symbol: str | None
    medium: str
    mode: str
    resolution: str
    tz: str
    start: datetime
    end: datetime
    groups: tuple[ObservationGroup, ...]
    tiers_used: tuple[int, ...]
    warnings: tuple[str, ...]

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "BestResult":
        return cls(
            feature=d.get("feature"), property=d["property"], unit_code=d.get("unitCode"),
            unit_symbol=d.get("unitSymbol"), medium=d["medium"], mode=d["mode"],
            resolution=d["resolution"], tz=d["tz"], start=_dt(d["from"]), end=_dt(d["to"]),
            groups=tuple(ObservationGroup.from_json(g) for g in d.get("groups") or ()),
            tiers_used=tuple(d.get("tiersUsed") or ()), warnings=tuple(d.get("warnings") or ()),
        )


@dataclass(frozen=True)
class Observation:
    """One stored, unfused observation (raw endpoint)."""

    t: datetime
    value: float
    observed_property: str
    feature_of_interest: str | None
    medium: str | None
    depth_from_cm: float | None
    depth_to_cm: float | None
    height_m: float | None
    organ: str | None
    context_ref: str | None
    deployment_id: str
    source_kind: str | None
    location: dict[str, Any] | None
    time_source: str | None
    quality_flag: str | None
    calibrated: bool
    device_id: str | None
    tier: int | None
    distance_m: float | None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Observation":
        return cls(
            t=_dt(d["t"]), value=d["value"], observed_property=d["observedProperty"],
            feature_of_interest=d.get("featureOfInterest"), medium=d.get("medium"),
            depth_from_cm=d.get("depthFromCm"), depth_to_cm=d.get("depthToCm"),
            height_m=d.get("heightM"), organ=d.get("organ"), context_ref=d.get("contextRef"),
            deployment_id=d["deploymentId"], source_kind=d.get("sourceKind"),
            location=d.get("location"), time_source=d.get("timeSource"),
            quality_flag=d.get("qualityFlag"), calibrated=bool(d.get("calibrated")),
            device_id=d.get("deviceId"), tier=d.get("tier"), distance_m=d.get("distanceM"),
        )


@dataclass(frozen=True)
class ObservableProperty:
    """A catalogue row (observation_properties) plus the derived UN/CEFACT ``unit_code``."""

    id: str
    tenant_id: str | None
    name: str
    domain: str
    unit: str
    unit_code: str | None
    default_medium: str
    allowed_media: tuple[str, ...]
    temporal_semantics: str
    valid_min: float | None
    valid_max: float | None
    max_rate_per_hour: float | None
    fusion: str
    nearby_radius_m: float | None
    max_age_s: int | None
    min_coverage: float | None
    sub_context_tolerance: dict[str, Any] = field(default_factory=dict)
    has_model: bool = False
    deprecated_at: datetime | None = None
    created_at: datetime | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "ObservableProperty":
        return cls(
            id=d["id"], tenant_id=d.get("tenantId"), name=d["name"], domain=d["domain"],
            unit=d["unit"], unit_code=d.get("unitCode"), default_medium=d["defaultMedium"],
            allowed_media=tuple(d.get("allowedMedia") or ()),
            temporal_semantics=d["temporalSemantics"], valid_min=d.get("validMin"),
            valid_max=d.get("validMax"), max_rate_per_hour=d.get("maxRatePerHour"),
            fusion=d.get("fusion", "median"), nearby_radius_m=d.get("nearbyRadiusM"),
            max_age_s=d.get("maxAgeS"), min_coverage=d.get("minCoverage"),
            sub_context_tolerance=dict(d.get("subContextTolerance") or {}),
            has_model=bool(d.get("hasModel")), deprecated_at=_dt(d.get("deprecatedAt")),
            created_at=_dt(d.get("createdAt")),
        )


# ---------------------------------------------------------- shared logic

def _iso_z(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _filters(
    *, feature: str | None, geometry: dict | None, property: str, start: datetime,
    end: datetime, medium: str | None, sub_context: dict | None, tiers: str,
    allow_medium_fallback: bool,
) -> dict[str, Any]:
    if (feature is None) == (geometry is None):
        raise ValueError("exactly one of feature or geometry is required")
    body: dict[str, Any] = {"property": property, "from": _iso_z(start), "to": _iso_z(end),
                            "tiers": tiers, "allowMediumFallback": bool(allow_medium_fallback)}
    if feature is not None:
        body["feature"] = feature
    else:
        body["geometry"] = geometry
    if medium is not None:
        body["medium"] = medium
    if sub_context:
        body["subContext"] = dict(sub_context)
    return body


def _best_body(*, mode: str, resolution: str, tz: str, **filters: Any) -> dict[str, Any]:
    body = _filters(**filters)
    body.update(mode=mode, resolution=resolution, tz=tz)
    return body


def _latest_window() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    return now - _LATEST_NOMINAL_WINDOW, now


def _raw_body(*, limit: int | None, quality_flags: list[str] | tuple[str, ...] | None,
              cursor: str | None, **filters: Any) -> dict[str, Any]:
    body = _filters(**filters)
    if limit is not None:
        body["limit"] = limit
    if quality_flags is not None:
        body["qualityFlags"] = list(quality_flags)
    if cursor:
        body["cursor"] = cursor
    return body


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.is_success:
        return
    message = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            message = str(payload.get("error") or payload.get("message") or "")
    except ValueError:
        pass
    if not message:
        message = resp.text[:500] or resp.reason_phrase
    raise ObservationsError(resp.status_code, message)


class _Base:
    def __init__(self, base_url: str | None, tenant_id: str, hmac_secret: str):
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not hmac_secret:
            raise ValueError("hmac_secret is required")
        self.tenant_id = tenant_id
        self.base_url = base_url or os.getenv("TIMESERIES_READER_URL", DEFAULT_BASE_URL)
        self._secret = hmac_secret

    def _headers(self) -> dict[str, str]:
        # signed per request: the signature carries a timestamp (300 s window)
        return {
            "X-Tenant-ID": self.tenant_id,
            "X-Auth-Signature": generate_hmac_signature(self._secret, "", self.tenant_id),
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return urljoin(f"{self.base_url}/", path.lstrip("/"))


# ------------------------------------------------------------------ async

class ObservationsClient(_Base):
    """Async client (httpx.AsyncClient)."""

    def __init__(self, base_url: str | None, tenant_id: str, hmac_secret: str):
        super().__init__(base_url, tenant_id, hmac_secret)
        self._client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ObservationsClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        resp = await self._client.request(method, self._url(path), headers=self._headers(),
                                          json=body)
        _raise_for_status(resp)
        return resp.json()

    async def best(
        self, *, feature: str | None = None, geometry: dict | None = None, property: str,
        start: datetime, end: datetime, mode: str = "series", resolution: str = "PT1H",
        tz: str = "UTC", medium: str | None = None, sub_context: dict | None = None,
        tiers: str = "all", allow_medium_fallback: bool = False,
    ) -> BestResult:
        body = _best_body(mode=mode, resolution=resolution, tz=tz, feature=feature,
                          geometry=geometry, property=property, start=start, end=end,
                          medium=medium, sub_context=sub_context, tiers=tiers,
                          allow_medium_fallback=allow_medium_fallback)
        return BestResult.from_json(await self._call("POST", "/api/observations/best", body))

    async def latest(
        self, *, feature: str, property: str, medium: str | None = None,
        sub_context: dict | None = None,
    ) -> BestResult:
        start, end = _latest_window()
        return await self.best(feature=feature, property=property, start=start, end=end,
                               mode="latest", medium=medium, sub_context=sub_context)

    async def raw(
        self, *, feature: str | None = None, geometry: dict | None = None, property: str,
        start: datetime, end: datetime, medium: str | None = None,
        sub_context: dict | None = None, tiers: str = "all",
        allow_medium_fallback: bool = False, quality_flags: list[str] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[Observation]:
        """Every stored observation, unfused. Follows ``nextCursor`` until null: a page may
        be short or empty while a cursor is still present."""
        cursor: str | None = None
        while True:
            body = _raw_body(limit=limit, quality_flags=quality_flags, cursor=cursor,
                             feature=feature, geometry=geometry, property=property,
                             start=start, end=end, medium=medium, sub_context=sub_context,
                             tiers=tiers, allow_medium_fallback=allow_medium_fallback)
            page = await self._call("POST", "/api/observations", body)
            for item in page.get("items") or ():
                yield Observation.from_json(item)
            cursor = page.get("nextCursor")
            if not cursor:
                return

    async def properties(self) -> list[ObservableProperty]:
        page = await self._call("GET", "/api/observations/properties")
        return [ObservableProperty.from_json(i) for i in page.get("items") or ()]


# ------------------------------------------------------------------ sync

class SyncObservationsClient(_Base):
    """Synchronous client (httpx.Client), same API as ``ObservationsClient``."""

    def __init__(self, base_url: str | None, tenant_id: str, hmac_secret: str):
        super().__init__(base_url, tenant_id, hmac_secret)
        self._client = httpx.Client(timeout=DEFAULT_TIMEOUT)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SyncObservationsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        resp = self._client.request(method, self._url(path), headers=self._headers(), json=body)
        _raise_for_status(resp)
        return resp.json()

    def best(
        self, *, feature: str | None = None, geometry: dict | None = None, property: str,
        start: datetime, end: datetime, mode: str = "series", resolution: str = "PT1H",
        tz: str = "UTC", medium: str | None = None, sub_context: dict | None = None,
        tiers: str = "all", allow_medium_fallback: bool = False,
    ) -> BestResult:
        body = _best_body(mode=mode, resolution=resolution, tz=tz, feature=feature,
                          geometry=geometry, property=property, start=start, end=end,
                          medium=medium, sub_context=sub_context, tiers=tiers,
                          allow_medium_fallback=allow_medium_fallback)
        return BestResult.from_json(self._call("POST", "/api/observations/best", body))

    def latest(
        self, *, feature: str, property: str, medium: str | None = None,
        sub_context: dict | None = None,
    ) -> BestResult:
        start, end = _latest_window()
        return self.best(feature=feature, property=property, start=start, end=end,
                         mode="latest", medium=medium, sub_context=sub_context)

    def raw(
        self, *, feature: str | None = None, geometry: dict | None = None, property: str,
        start: datetime, end: datetime, medium: str | None = None,
        sub_context: dict | None = None, tiers: str = "all",
        allow_medium_fallback: bool = False, quality_flags: list[str] | None = None,
        limit: int | None = None,
    ) -> Iterator[Observation]:
        cursor: str | None = None
        while True:
            body = _raw_body(limit=limit, quality_flags=quality_flags, cursor=cursor,
                             feature=feature, geometry=geometry, property=property,
                             start=start, end=end, medium=medium, sub_context=sub_context,
                             tiers=tiers, allow_medium_fallback=allow_medium_fallback)
            page = self._call("POST", "/api/observations", body)
            for item in page.get("items") or ():
                yield Observation.from_json(item)
            cursor = page.get("nextCursor")
            if not cursor:
                return

    def properties(self) -> list[ObservableProperty]:
        page = self._call("GET", "/api/observations/properties")
        return [ObservableProperty.from_json(i) for i in page.get("items") or ()]
