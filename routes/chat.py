from fastapi import APIRouter
from models.message import Message
from services.ai_service import generar_respuesta
from services.pdf_generate import generar_pdf
from fastapi.responses import FileResponse

router = APIRouter()

@router.post("/chat")
async def chat(data: Message):
    mensaje = data.message.lower()
    
    # Detectar intención de generar PDF
    if "pdf" in mensaje:
        info = {
            "Nombre": "Usuario GIA",
            "Solicitud": "Generación de documento",
            "Grupo": "GIA UFPS"
        }
        archivo = generar_pdf(info)
        return {
            "reply": f"📄 Tu PDF ha sido generado correctamente.\nPuedes descargarlo aquí: http://localhost:8000/download/{archivo}"
        }
    
    # Si no es PDF, usar la IA normalmente
    reply = generar_respuesta(data.message, data.history)
    return {"reply": reply}

@router.get("/download/{filename}")
def download_file(filename: str):
    return FileResponse(path=filename, filename=filename)