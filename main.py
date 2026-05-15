from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from services.crear_docentes import crear_docentes
from routes.chat import router as chat_router

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    crear_docentes()


app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root(request: Request):
    
    return FileResponse("index.html", media_type="text/html")


app.include_router(chat_router)
