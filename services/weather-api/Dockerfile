FROM python:3.11-slim
LABEL org.opencontainers.image.source=https://github.com/nkz-os/nkz

WORKDIR /app

# System deps for psycopg2
RUN apt-get update && apt-get install -y gcc libpq-dev curl && rm -rf /var/lib/apt/lists/*

COPY services/weather-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Common modules (auth, db_helper, weather_utils)
COPY services/common /app/common

# Service code
COPY services/weather-api /app/weather-api

# Ensure /app is on Python path
ENV PYTHONPATH=/app

RUN useradd -m -u 1000 apiuser && chown -R apiuser:apiuser /app
USER apiuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "weather-api.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
