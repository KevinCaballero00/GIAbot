# ── Base compartida ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Directorios de datos persistentes (los volúmenes se montan sobre estos)
RUN mkdir -p /data static/extracciones static/generados

# ── Full: frontend + API ───────────────────────────────────────────────────────
FROM base AS full
ENV SERVE_FRONTEND=true
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Bot: solo API, sin página de prueba ────────────────────────────────────────
FROM base AS bot
ENV SERVE_FRONTEND=false
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
