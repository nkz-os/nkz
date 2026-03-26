FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema necesarias para compilar/procesar
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el paquete task-queue del core (requiere contexto en la raíz)
COPY . /app

CMD ["python", "main.py"]
