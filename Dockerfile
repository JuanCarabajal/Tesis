# -------- Etapa 1: compilar el parser en Go --------
FROM golang:1.22 AS gobuilder
WORKDIR /src/parser-go

# Copiamos mod y vendor primero para cache
COPY parser-go/go.mod parser-go/go.sum ./
COPY parser-go/vendor ./vendor

# Copiamos el resto del código
COPY parser-go/ ./

# Compilar binario Linux, estático
ENV CGO_ENABLED=0 GOOS=linux GOARCH=amd64
RUN go build -mod=vendor -o /out/cs2dem .

# -------- Etapa 2: imagen final de Python --------
FROM python:3.11-slim
WORKDIR /app

COPY requirements.txt Procfile ./
COPY web/ ./web/
COPY configs/ ./configs/
COPY --from=gobuilder /out/cs2dem /app/bin/cs2dem

RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=10000
CMD gunicorn web.app:app -b 0.0.0.0:${PORT}
