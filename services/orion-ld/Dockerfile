# =============================================================================
# Orion-LD develop — includes the empty-DB /types hang fix (upstream 4cb23b5a)
# =============================================================================
# Why this image exists
# ---------------------
# fiware/orion-ld:1.12.0 hangs a worker thread forever when GET /ngsi-ld/v1/types
# hits a tenant whose entities collection is empty: dbEntityTypesGet treats an
# empty result as "fall back to the legacy C++ driver", but with -mongocOnly that
# driver's pool is never initialized, so the call blocks on a never-posted
# semaphore. Our parcel reconciler wedged one worker every ~40s until the broker
# stopped answering /version and the liveness probe restarted the pod (~33 min).
#
# The fix is upstream commit 4cb23b5a ("fix(mongocOnly): GET /types no longer
# hangs on an empty database"), in develop since 2026-08-13 but NOT in any
# release — 1.12.0 predates it by ~7 months.
#
# Build strategy: build develop (shallow clone, HEAD contains the fix) with the
# upstream build path — the public fiware/orion-ld-base dependency image +
# docker/build.sh -b (compile only, no test stage). We pin ORIONLD_REF to a
# specific commit for reproducibility. Runtime stage mirrors upstream
# docker/Dockerfile-ubi exactly (same runtime libs, ssl certs, ldcontexts).
#
# NOTE: develop is upstream's integration branch (ahead of 1.12.0). We accept it
# because 1.12.0 is unfixable for this bug without a rebuild and the fix has no
# release; pin to a known-good commit and validate before promoting.
#
# REMOVE THIS IMAGE when upstream cuts a release containing 4cb23b5a
# (expected 1.13.0). Track: https://github.com/FIWARE/context.Orion-LD
#
# Build (context = this directory; the Dockerfile clones upstream itself):
#   docker build -t ghcr.io/nkz-os/nkz/orion-ld:develop-nkz1 services/orion-ld/
# =============================================================================

# --- Stage 1: fetch upstream develop (shallow) at a pinned commit ------------
FROM alpine/git AS src
# Pin to the develop HEAD verified to contain 4cb23b5a on 2026-08-30. With a
# --depth 1 clone of develop, only the tip commit is present, so the ref must
# match the current develop tip; bump it (and re-validate) to move forward.
ARG ORIONLD_REF=6c86d8bb0d7fffe39418a68c5f2bac1c6973da34
WORKDIR /src
RUN git clone --depth 1 --branch develop https://github.com/FIWARE/context.Orion-LD.git . \
 && git checkout -q ${ORIONLD_REF}

# --- Stage 2: compile against the prebuilt dependency base -------------------
FROM fiware/orion-ld-base AS build-stage
ARG PATH_TO_SRC='/opt/orion/'
COPY --from=src /src ${PATH_TO_SRC}
WORKDIR ${PATH_TO_SRC}
# build.sh lives in the cloned tree (docker/build.sh); it is NOT in the base
# image's /tmp, so invoke it from the source tree.
RUN docker/build.sh -b

# --- Stage 3: runtime (mirrors upstream docker/Dockerfile-ubi) ----------------
FROM registry.access.redhat.com/ubi8/ubi
LABEL description="Orion-LD develop including the empty-DB /types hang fix (upstream 4cb23b5a). Remove when upstream releases it." \
      nkz.base="FIWARE/context.Orion-LD@develop" \
      nkz.fix="FIWARE/context.Orion-LD@4cb23b5a"

COPY --from=build-stage /etc/ssl/ /etc/ssl/
COPY --from=build-stage /usr/bin/orionld /usr/bin
COPY --from=build-stage /usr/local/lib/ /usr/lib64/
COPY --from=build-stage /usr/local/lib64/ /usr/lib64/
COPY --from=build-stage /usr/lib/librdkafka* /usr/lib64/
COPY --from=build-stage /opt/orion/ldcontexts/ /opt/orion/ldcontexts/

COPY repos/other-places.repo /etc/yum.repos.d/
COPY repos/ubi.repo /etc/yum.repos.d/

RUN cd /etc/pki/rpm-gpg && \
    curl -O https://repo.almalinux.org/almalinux/RPM-GPG-KEY-AlmaLinux && \
    yum -y --nogpgcheck install https://download.postgresql.org/pub/repos/yum/reporpms/EL-8-x86_64/pgdg-redhat-repo-latest.noarch.rpm && \
    dnf -y module disable postgresql && \
    yum -y --nogpgcheck install epel-release && \
    dnf config-manager --set-enabled powertools && \
    yum install -y libicu libpq5 boost tinyxml2 yaml-cpp && \
    yum clean all && \
    ldconfig

# Entrypoint mirrors upstream; our -mongocOnly/-subCacheIval flags come from the
# Deployment args (gitops-config), which override the CMD.
ENTRYPOINT ["orionld", "-fg", "-multiservice", "-ngsiv1Autocast"]
EXPOSE 1026
