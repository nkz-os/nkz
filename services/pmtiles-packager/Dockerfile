FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema necesarias para compilar/procesar
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/pmtiles-packager/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código del servicio + task-queue (requiere contexto en la raíz del repo)
COPY services/pmtiles-packager /app/services/pmtiles-packager
COPY services/task-queue /app/services/task-queue

WORKDIR /app/services/pmtiles-packager

CMD ["python", "main.py"]
