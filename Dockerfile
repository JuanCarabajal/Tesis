# -------- Etapa 1: compilar el parser en Go --------
FROM golang:1.22 AS gobuilder
WORKDIR /src/parser-go

# Copiamos primero los manifests para cachear dependencias
COPY parser-go/go.mod parser-go/go.sum ./
RUN go mod download

# Ahora sí, copiamos el resto del código
COPY parser-go/ ./

# Compilar binario Linux, estático (sin CGO)
ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64
RUN go build -o /out/cs2dem .

# -------- Etapa 2: imagen final de Python --------
FROM python:3.11-slim
WORKDIR /app

# (opcional) paquetes del sistema si hicieran falta
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copiamos solo lo necesario
COPY requirements.txt Procfile ./
COPY web/ ./web/
COPY configs/ ./configs/

# Binario compilado (Linux) desde la etapa Go
COPY --from=gobuilder /out/cs2dem /app/bin/cs2dem

# Dependencias Python
RUN pip install --no-cache-dir -r requirements.txt

# Render inyecta $PORT
ENV PORT=10000
CMD gunicorn web.app:app -b 0.0.0.0:${PORT}
