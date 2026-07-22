# ── Base compartida ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

# Instalar dependencias en capa separada del código (mejor uso del caché)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código de la aplicación
COPY . .

# Directorios de datos persistentes (se montan como volúmenes en producción)
RUN mkdir -p static/extracciones static/generados

# ── Imagen completa: página de prueba + API ────────────────────────────────────
FROM base AS full
LABEL org.opencontainers.image.description="GIAbot completo: frontend demo + API"

ENV SERVE_FRONTEND=true

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Imagen bot: solo API, para embeber en gia.ufps.edu.co ─────────────────────
#
# Esta imagen NO sirve el index.html de prueba; solo expone los endpoints:
#   POST /chat           ← el widget del cliente llama aquí
#   GET  /descargar/...  ← descarga de PDFs generados
#   GET  /health         ← health check del servidor
#   GET  /static/...     ← CSS/JS del widget y PDFs generados
#   GET  /admin/...      ← panel de administración
#
# Variables de entorno requeridas en producción:
#   GOOGLE_API_KEY   → clave de la API de Gemini
#   DATABASE_URL     → conexión PostgreSQL (postgresql://user:pass@host:5432/db)
#   ALLOWED_ORIGINS  → orígenes CORS separados por coma (ej: https://gia.ufps.edu.co)
# ──────────────────────────────────────────────────────────────────────────────
FROM base AS bot
LABEL org.opencontainers.image.description="GIAbot API — para embeber en gia.ufps.edu.co"

ENV SERVE_FRONTEND=false
# Por defecto permite el sitio oficial del GIA; ajustar si el bot se sirve desde otro dominio
ENV ALLOWED_ORIGINS="https://gia.ufps.edu.co,https://www.gia.ufps.edu.co"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
