FROM python:3.11-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

COPY services/sensor-health-beat/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/sensor-health-beat/sensor_health_beat ./sensor_health_beat

EXPOSE 8000

CMD ["uvicorn", "sensor_health_beat.app:app", "--host", "0.0.0.0", "--port", "8000"]
# Ensure CI can push this package to GHCR
