FROM python:3.11-slim
WORKDIR /app

# Copiamos solo lo necesario
COPY requirements.txt Procfile ./
COPY web/ ./web/
COPY configs/ ./configs/
COPY bin/cs2dem /app/bin/cs2dem

# Dependencias Python
RUN pip install --no-cache-dir -r requirements.txt

# Asegurar permisos de ejecución del binario (Windows no los preserva)
RUN chmod +x /app/bin/cs2dem

ENV PORT=10000
CMD gunicorn web.app:app -b 0.0.0.0:${PORT}
