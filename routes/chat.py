from fastapi import APIRouter
from models.message import Message
from services.ai_service import generar_respuesta
from services.pdf_generate import generar_pdf
from fastapi.responses import FileResponse

router = APIRouter()

def extraer_informacion_para_pdf(mensaje, historial):
    # Por ahora extrae info básica del historial
    info = {"Solicitud": mensaje}
    for i, h in enumerate(historial[-4:]):  # últimos 4 mensajes
        info[f"Mensaje {i+1}"] = h["content"]
    return info

@router.post("/chat")
async def chat(data: Message):
    mensaje = data.message.lower()
    
    if "pdf" in mensaje:
        info = extraer_informacion_para_pdf(data.message, data.history)
        archivo = generar_pdf(info)
        return {
            "reply": f"📄 Tu PDF ha sido generado.\nDescárgalo aquí: http://localhost:8000/download/{archivo}"
        }
    
    reply = generar_respuesta(data.message, data.history)
    return {"reply": reply}

@router.get("/download/{filename}")
def download_file(filename: str):
    return FileResponse(path=filename, filename=filename)