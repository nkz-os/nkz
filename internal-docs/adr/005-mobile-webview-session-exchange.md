---
title: "ADR 005 — Mobile WebView session exchange (one-time ticket)"
description: "Secure bridge between native Keycloak JWT and host HttpOnly cookie session without postMessage credential leaks."
---

# ADR 005: Puente de autenticación seguro para WebViews híbridos (session exchange)

## Status

Proposed — not implemented. Supersedes any design that injected JWT into the SPA via `postMessage` / `localStorage`.

## Context

The native app (`nkz-mobile`) obtains an OAuth access token (JWT) via Keycloak (PKCE). Business modules (e.g. risks, DataHub, Odoo) may load inside a `WebView`. The host web app (`nkz/apps/host`) uses **HttpOnly** cookies for session integrity and XSS resistance.

Passing the native JWT into the web layer via `postMessage` and storing it in memory or `localStorage` would:

- Introduce a severe XSS exposure surface.
- Split authentication into two divergent frontend models.

## Decision

**Reject** credential transfer via `postMessage` to the SPA.

Implement a **session exchange** using a **one-time opaque ticket**:

1. **Native (`POST /api/auth/mobile-ticket`)** — Request body may include routing hints (e.g. `target_module`). Headers: `Authorization: Bearer <native JWT>`. Gateway validates JWT.

2. **Backend** — Generate a cryptographically random opaque token (e.g. UUID v4), store in **Redis** keyed with tenant/user context, **TTL ≈ 15s**, return ticket to the client only.

3. **WebView URL** — Load a **host** URL such as  
   `https://nekazari.robotika.cloud/api/auth/consume-ticket?ticket=<UUID>&redirect=/module/risk`  
   (exact path owned by gateway or host; must be HTTPS, SameSite-safe).

4. **Consume (`GET`)** — Gateway validates ticket, **deletes** it on success (one-time), issues **302** to `redirect` with **`Set-Cookie`** for the standard web session cookie (HttpOnly, Secure, SameSite=Lax as appropriate).

## Consequences

- **No JWT in JS** on the web side; existing cookie-based API calls continue unchanged.
- **Short TTL + one-shot** limits interception value of a leaked URL.
- **New infrastructure**: Redis keyspace, rate limits, monitoring for ticket mint/consume failures.
- **Ownership**: Clarify whether `consume-ticket` is implemented in api-gateway only or with host redirects; CORS does not apply to full-page WebView navigation the same way as XHR.

## Follow-up work

- Threat model: phishing copies of the consume URL, clipboard leaks, shared devices.
- Align with Keycloak session lifetime vs platform cookie lifetime.
- Optional: bind ticket to WebView user-agent or attestation in a later hardening phase.
