FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY services/timeseries-reader/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy common services as a package. PYTHONPATH must expose BOTH forms:
# /app/common for flat imports (auth_middleware, api_errors) and /app for
# package imports (common.ngsi_headers, required by urn_resolution — a flat
# /common copy left URN resolution silently unavailable at runtime and every
# /v2 URN read returned 503).
COPY services/common/ /app/common/
ENV PYTHONPATH=/app:/app/common

# Copy application code
COPY services/timeseries-reader/app.py services/timeseries-reader/urn_resolution.py services/timeseries-reader/gdd_response.py ./

# Create non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 5000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Run with gunicorn in production
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "2", "--timeout", "60", "app:app"]
