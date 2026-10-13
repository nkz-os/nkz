"""
ModuleLifecycle — base class for module lifecycle hooks.

The platform calls lifecycle webhooks via HMAC-signed HTTP POST.
Modules extend this class and implement on_install/on_uninstall/etc.

entity-manager POSTs module.enabled / module.disabled once, without retry.
Treat it as a hint and keep a periodic reconcile
(SubscriptionRegistrar.periodic_reconcile) as the source of truth.
"""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from nkz_platform_sdk.notify_auth import accept_master_from_env, verify_notification

logger = logging.getLogger(__name__)


@dataclass
class LifecycleResult:
    status: str  # "active" | "failed" | "pending"
    message: str = ""
    resources: dict[str, Any] = field(default_factory=dict)


class ModuleLifecycle:
    """Base class for module lifecycle hooks. Override any hook needed."""

    def __init__(self):
        self._handlers: dict[str, Any] = {}

    def on_install(self, func):
        self._handlers["install"] = func
        return func

    def on_uninstall(self, func):
        self._handlers["uninstall"] = func
        return func

    def on_enable(self, func):
        self._handlers["enable"] = func
        return func

    def on_disable(self, func):
        self._handlers["disable"] = func
        return func

    async def handle(
        self, event: str, tenant_id: str, config: dict | None = None
    ) -> LifecycleResult:
        handler = self._handlers.get(event)
        if handler is None:
            return LifecycleResult(
                status="active",
                message=f"No handler registered for '{event}' — noop",
            )
        try:
            return await handler(tenant_id, config or {})
        except Exception as e:
            return LifecycleResult(status="failed", message=str(e))


async def _run_reconcile(reconcile: Callable[[], Awaitable[object]]) -> None:
    try:
        await reconcile()
    except Exception as e:  # a hint must never take the receiver down
        logger.warning("Lifecycle hint reconcile failed: %s", e)


class _HintCoalescer:
    """At most one reconcile running and one pending, plus one delayed follow-up.

    A burst of hints costs one extra pass, not one per hint. The follow-up
    pass after ``followup_seconds`` covers what one process cannot see: a
    heal on another replica that acted on an older snapshot, and Orion's
    eventually consistent subscription reads right after a change.
    """

    def __init__(self, reconcile: Callable[[], Awaitable[object]], followup_seconds: float):
        self._reconcile = reconcile
        self._followup = followup_seconds
        self._dirty = False
        self._task: asyncio.Task | None = None

    def poke(self) -> None:
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._drain())

    async def _drain(self) -> None:
        while True:
            while self._dirty:
                self._dirty = False
                await _run_reconcile(self._reconcile)
            if self._followup <= 0:
                return
            await asyncio.sleep(self._followup)
            await _run_reconcile(self._reconcile)
            if not self._dirty:
                return

    async def wait_idle(self) -> None:
        while self._task is not None and not self._task.done():
            await asyncio.wait({self._task})


def lifecycle_hint_router(
    path: str,
    reconcile: Callable[[], Awaitable[object]],
    *,
    secret: str | None = None,
    followup_seconds: float = 90.0,
) -> APIRouter:
    """Receiver for entity-manager's module.enabled / module.disabled hint.

    A verified delivery schedules ``reconcile()`` and answers 202; the body is
    not read, so a replayed or stale event only costs one extra reconcile.
    Hints are coalesced and followed by one more pass after
    ``followup_seconds`` (see ``_HintCoalescer``). Auth: derived notification
    token bound to tenant + Host (``notify_auth.verify_notification``); the
    master secret is accepted while ``NKZ_NOTIFY_ACCEPT_MASTER`` allows it.
    Mount it under the module's ``/internal/`` prefix, exempt from user auth,
    and declare its URL as ``marketplace_modules.metadata.lifecycle_webhook_url``.
    """
    router = APIRouter()
    hints = _HintCoalescer(reconcile, followup_seconds)

    @router.post(path, status_code=202)
    async def lifecycle_hint(request: Request) -> dict:
        key = secret if secret is not None else os.getenv("INTERNAL_SERVICE_SECRET", "")
        if not verify_notification(
            request.headers, secret=key, accept_master=accept_master_from_env()
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        hints.poke()
        return {"accepted": True}

    return router
