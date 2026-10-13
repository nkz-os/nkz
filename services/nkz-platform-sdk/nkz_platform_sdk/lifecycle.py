"""
ModuleLifecycle — base class for module lifecycle hooks.

The platform calls lifecycle webhooks via HMAC-signed HTTP POST.
Modules extend this class and implement on_install/on_uninstall/etc.

entity-manager POSTs module.enabled / module.disabled once, without retry.
Treat it as a hint and keep a periodic reconcile
(SubscriptionRegistrar.periodic_reconcile) as the source of truth.
"""

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

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


def lifecycle_hint_router(
    path: str,
    reconcile: Callable[[], Awaitable[object]],
    *,
    secret: str | None = None,
) -> APIRouter:
    """Receiver for entity-manager's module.enabled / module.disabled hint.

    A verified delivery schedules ``reconcile()`` and answers 202; the body is
    not read, so a replayed or stale event only costs one extra reconcile.
    Auth: derived notification token bound to tenant + Host
    (``notify_auth.verify_notification``); the master secret is accepted while
    ``NKZ_NOTIFY_ACCEPT_MASTER`` allows it. Mount it under the module's
    ``/internal/`` prefix, exempt from user auth, and declare its URL as
    ``marketplace_modules.metadata.lifecycle_webhook_url``.
    """
    router = APIRouter()

    @router.post(path, status_code=202)
    async def lifecycle_hint(request: Request, background: BackgroundTasks) -> dict:
        key = secret if secret is not None else os.getenv("INTERNAL_SERVICE_SECRET", "")
        if not verify_notification(
            request.headers, secret=key, accept_master=accept_master_from_env()
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        background.add_task(_run_reconcile, reconcile)
        return {"accepted": True}

    return router
