# =============================================================================
# Keycloak with PostgreSQL Driver and Script Mappers - Quarkus Build Method
# =============================================================================

FROM quay.io/keycloak/keycloak@sha256:9409c59bdfb65dbffa20b11e6f18b8abb9281d480c7ca402f51ed3d5977e6007

# Copy PostgreSQL driver to providers directory
COPY services/keycloak/postgresql-42.7.1.jar /opt/keycloak/providers/

# Copy script mappers JAR to providers directory
# The JAR contains the JavaScript mapper script and META-INF descriptor
COPY services/keycloak/keycloak-script-mappers.jar /opt/keycloak/providers/

# Login / registration UI theme (extends keycloak.v2; baked in at build for optimized mode)
COPY services/keycloak/themes/nkz /opt/keycloak/themes/nkz

# Build Keycloak with PostgreSQL driver and script mappers
# Enable scripts feature for script-based protocol mappers
# Include all runtime options in build to avoid configuration mismatch
RUN /opt/keycloak/bin/kc.sh build \
    --db=postgres \
    --features=scripts \
    --http-relative-path=/auth \
    --health-enabled=true \
    --transaction-xa-enabled=false

# Switch to keycloak user
USER keycloak

# Expose port
EXPOSE 8080

# Start Keycloak in optimized mode
CMD ["start", "--optimized"]