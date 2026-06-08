import asyncio
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.admin import router as admin_router
from routes.chat import router as chat_router
from services.crear_docentes import crear_docentes

load_dotenv()

logger = logging.getLogger(__name__)

SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "true").lower() == "true"
_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",")]

app = FastAPI(title="GIAbot API", version="2.0.0")


# ── Job periódico de refresco FO-IN-17 ───────────────────────────────────────

async def _job_refresco_fo_in_17() -> None:
    """
    Verifica cada 24 horas si algún registro FO-IN-17 supera los 15 días
    sin refresco y lo actualiza. Los errores se registran sin borrar la
    última versión válida.
    """
    INTERVALO_SEGUNDOS = 24 * 3600
    while True:
        await asyncio.sleep(INTERVALO_SEGUNDOS)
        try:
            from services.fo_in_17_service import refrescar_todos
            logger.info("Job periódico: verificando registros FO-IN-17 para refresco...")
            resultados = await asyncio.to_thread(refrescar_todos)
            if resultados:
                logger.info("Job periódico: %d registros FO-IN-17 procesados", len(resultados))
        except Exception as exc:
            logger.error("Job periódico: error en refresco de FO-IN-17: %s", exc)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    # 1. Crear/actualizar docentes en BD (también llama a init_db)
    try:
        crear_docentes()
    except Exception as exc:
        logger.error("Startup: error al crear docentes: %s", exc)

    # 2. Poblar knowledge_chunks con el contexto web ya cacheado
    try:
        from services.ai_service import CONTEXTO_WEB
        from services.rag_service import poblar_chunks
        if CONTEXTO_WEB and CONTEXTO_WEB.strip():
            n = await asyncio.to_thread(poblar_chunks, CONTEXTO_WEB)
            logger.info("Startup: %d knowledge_chunks inicializados.", n)
        else:
            logger.warning("Startup: CONTEXTO_WEB vacío, chunks RAG no poblados.")
    except Exception as exc:
        logger.warning("Startup: no se pudieron poblar los chunks RAG: %s", exc)

    # 3. Job periódico de refresco
    asyncio.create_task(_job_refresco_fo_in_17())


# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Archivos estáticos y frontend ─────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

if SERVE_FRONTEND:
    @app.get("/")
    def read_root():
        return FileResponse("index.html", media_type="text/html")


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    from services.ai_service import client as gemini_client
    return {
        "status": "ok",
        "gemini": "disponible" if gemini_client is not None else "no disponible",
    }


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(chat_router)
app.include_router(admin_router)
