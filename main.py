import asyncio
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.chat import router as chat_router
from services.crear_docentes import crear_docentes

load_dotenv()

logger = logging.getLogger(__name__)

SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "true").lower() == "true"
_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",")]

app = FastAPI()


async def _job_refresco_fo_in_17() -> None:
    """
    Verifica cada 24 horas si algún registro FO-IN-17 supera los 15 días
    sin refresco y lo actualiza. Los errores se registran sin borrar la
    última versión válida (ver fo_in_17_service.generar_fo_in_17).
    """
    INTERVALO_SEGUNDOS = 24 * 3600
    while True:
        await asyncio.sleep(INTERVALO_SEGUNDOS)
        try:
            from services.fo_in_17_service import refrescar_todos
            logger.info("Job periódico: verificando registros FO-IN-17 para refresco...")
            resultados = await asyncio.to_thread(refrescar_todos)
            if resultados:
                logger.info(
                    "Job periódico: %d registros FO-IN-17 procesados", len(resultados)
                )
        except Exception as exc:
            logger.error("Job periódico: error en refresco de FO-IN-17: %s", exc)


@app.on_event("startup")
async def startup_event():
    crear_docentes()
    asyncio.create_task(_job_refresco_fo_in_17())


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

if SERVE_FRONTEND:
    @app.get("/")
    def read_root():
        return FileResponse("index.html", media_type="text/html")


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(chat_router)
