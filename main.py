import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.chat import router as chat_router
from services.crear_docentes import crear_docentes

load_dotenv()

SERVE_FRONTEND = os.getenv("SERVE_FRONTEND", "true").lower() == "true"
_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS = [o.strip() for o in _origins_raw.split(",")]

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    crear_docentes()


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
