# Cookie and storage inventory (Nekazari host) — 2026-05-05

## Scope

Open-source web host (`nkz/apps/host`), API gateway session endpoint, and public policy pages on `nkz-os-website`. Internal deployments may add module-specific cookies; this list is the **core** stack.

| Key | Mechanism | Category | Notes |
| --- | --- | --- | --- |
| `nkz_token` | HTTP cookie (HttpOnly, Secure, SameSite=Strict) | Strictly necessary | Set by `POST /api/auth/session` after Keycloak validation; session for API calls. |
| Keycloak / OIDC | HTTP cookies (IdP) | Strictly necessary | Issued by the identity provider during login; see IdP admin docs. |
| `nkz_cookie_consent_v2` | `localStorage` | Consent record | JSON: policy notice version + analytics flag + timestamp. |
| Legacy `cookieConsent` | `localStorage` | Migrated | Migrated to `nkz_cookie_consent_v2` on first read (conservative: no analytics from legacy "accept"). |
| Optional analytics | Script / cookies | Optional | Gated by `CookieConsentContext.analyticsEnabled`; load point: `AnalyticsConsentRoot`. |

## Policy surfaces

- EN: `https://nkz-os.org/legal/cookies`
- ES: `https://nkz-os.org/es/legal/cookies`

## Versioning

`COOKIE_POLICY_NOTICE_VERSION` in `CookieConsentContext.tsx` — increment to re-prompt users after material policy changes.
