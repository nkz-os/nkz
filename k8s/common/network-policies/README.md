# NetworkPolicy templates

The `.yaml` files in this directory are **reference templates** for the
baseline NetworkPolicy posture used by a Nekazari deployment. They are
not what runs in `nkz-os/nkz`'s own production cluster.

## Where the live policies live

The policies actually applied to the production cluster are managed by
ArgoCD from a separate private overlay repository:

- App: `core-network-policies`
- Source: `nkz-os/gitops-config` → `overlays/core/network-policies/`

That overlay is the source of truth for which ports the operator allows
Traefik to reach, which workloads can talk to MinIO/MongoDB/Mosquitto,
and any deployment-specific tightening. Changes to it are kept private
because the precise port list is operational configuration for *that*
deployment, not part of the project itself.

## Using the templates

For a new Nekazari deployment, start from the relevant template, copy
it into your own private GitOps overlay, adjust ports/selectors for the
modules you have enabled, and let ArgoCD reconcile it.

The templates here intentionally over-document the rationale so a fresh
operator can pick the right policy without reverse-engineering it.
