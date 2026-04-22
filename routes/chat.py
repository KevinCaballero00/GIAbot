from fastapi import APIRouter
from models.message import Message
from services.ai_service import generar_respuesta
from services.auth_service import verificar_credenciales
from fastapi.responses import FileResponse
import re

router = APIRouter()

# ── Sesiones activas en memoria { session_id: {docente, estado} } ─────────────
# La session_id se construye desde el frontend (ver nota abajo)
sesiones_activas: dict = {}

# ── Alias de los PDFs ─────────────────────────────────────────────────────────
ALIASES_13 = [
    "fo-in-13", "fo in 13", "foin13", "informe 13", "número 13",
    "numero 13", "informe gestion", "gestión grupos", "gestion grupos",
    "informe de gestión", "informe de gestion"
]

ALIASES_17 = [
    "fo-in-17", "fo in 17", "foin17", "informe 17", "número 17",
    "numero 17", "plan de accion", "plan de acción", "plan accion",
    "plan acción"
]

VERBOS_SOLICITUD = [
    "genera", "généra", "envía", "envia", "manda", "dame", "necesito",
    "quiero", "descarga", "obten", "obtén", "proporciona", "muéstrame",
    "muestrame", "pásamelo", "pasamelo", "ahora", "también", "tambien"
]


def detectar_pdf_solicitado(mensaje: str, historial: list):
    msg = mensaje.lower()
    pide_13 = any(alias in msg for alias in ALIASES_13)
    pide_17 = any(alias in msg for alias in ALIASES_17)

    palabras_ambos = ["ambos", "los dos", "todos los", "ambos informes", "los dos informes"]
    pide_ambos = any(p in msg for p in palabras_ambos)

    tiene_verbo = any(v in msg for v in VERBOS_SOLICITUD)
    tiene_referencia_pdf = re.search(
        r"(fo-in-13|fo-in-17|foin13|foin17|informe de gestion|plan de accion"
        r"|pdf|informe|formulario|documento|plan|el 13|el 17|número 13|número 17"
        r"|numero 13|numero 17)",
        msg
    )
    es_solicitud_pdf = tiene_verbo and tiene_referencia_pdf

    if not pide_13 and not pide_17 and not pide_ambos:
        ultimo_bot = next(
            (h["content"] for h in reversed(historial) if h["role"] == "assistant"),
            ""
        )
        if "FO-IN-13" in ultimo_bot or "FO-IN-17" in ultimo_bot:
            if re.search(r"\b17\b", msg):
                pide_17 = True
            if re.search(r"\b13\b", msg):
                pide_13 = True

    if not es_solicitud_pdf and not pide_13 and not pide_17:
        return []

    if pide_ambos:
        return [13, 17]
    if pide_13 and pide_17:
        return [13, 17]
    if pide_13:
        return [13]
    if pide_17:
        return [17]
    return []


def construir_respuesta_pdfs(pdfs: list) -> str:
    """Construye el mensaje con los enlaces de descarga."""
    enlaces = []
    if 13 in pdfs:
        enlaces.append(
            "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
            "👉 [Descargar PDF](/static/docs/FO-IN-13%20INFORME%20GESTION%20GRUPOS%20INV%20V1.pdf)"
        )
    if 17 in pdfs:
        enlaces.append(
            "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
            "👉 [Descargar PDF](/static/docs/FO-IN-17%20PLAN%20DE%20ACCION%20GRUPOS%20INV%20V1.pdf)"
        )
    intro = "Aquí tienes los documentos solicitados:" if len(enlaces) == 2 else "Aquí tienes el documento solicitado:"
    return intro + "\n\n" + "\n\n".join(enlaces)


@router.post("/chat")
async def chat(data: Message):
    mensaje = data.message
    # Usamos el último mensaje del historial como session_id simple
    # En producción se puede reemplazar por un token real
    session_id = data.session_id if hasattr(data, "session_id") else "default"

    estado = sesiones_activas.get(session_id)

    # ── Flujo de autenticación en curso ──────────────────────────────────────
    if estado:
        if estado["paso"] == "esperando_usuario":
            sesiones_activas[session_id]["usuario_ingresado"] = mensaje.strip()
            sesiones_activas[session_id]["paso"] = "esperando_password"
            return {"reply": "🔐 Ahora ingresa tu contraseña:"}

        if estado["paso"] == "esperando_password":
            usuario = estado["usuario_ingresado"]
            password = mensaje.strip()
            docente = verificar_credenciales(usuario, password)

            if docente:
                sesiones_activas[session_id]["autenticado"] = True
                sesiones_activas[session_id]["docente"] = docente
                sesiones_activas[session_id]["paso"] = "autenticado"
                pdfs = estado["pdfs_solicitados"]
                return {
                    "reply": f"✅ Bienvenido/a, **{docente['nombre']}**. Acceso verificado.\n\n"
                             + construir_respuesta_pdfs(pdfs)
                }
            else:
                # Credenciales incorrectas: limpiar sesión
                del sesiones_activas[session_id]
                return {
                    "reply": "❌ Usuario o contraseña incorrectos. "
                             "Si deseas intentarlo de nuevo, vuelve a solicitar el documento."
                }

    # ── Docente ya autenticado en esta sesión ─────────────────────────────────
    autenticado = (
        estado is not None and
        estado.get("autenticado") and
        estado.get("paso") == "autenticado"
    )

    # ── Detección de solicitud de PDFs ────────────────────────────────────────
    pdfs_solicitados = detectar_pdf_solicitado(mensaje, data.history)

    if pdfs_solicitados:
        if autenticado:
            # Ya está autenticado, entrega directamente
            return {"reply": construir_respuesta_pdfs(pdfs_solicitados)}
        else:
            # Iniciar flujo de autenticación
            sesiones_activas[session_id] = {
                "paso": "esperando_usuario",
                "pdfs_solicitados": pdfs_solicitados,
                "usuario_ingresado": None,
                "autenticado": False,
                "docente": None,
            }
            return {
                "reply": "🔒 Para acceder a los documentos del semillero necesito verificar "
                         "tu identidad.\n\n👤 Por favor ingresa tu **usuario**:"
            }

    # ── Respuesta normal del bot ──────────────────────────────────────────────
    reply = generar_respuesta(mensaje, data.history)
    return {"reply": reply}


@router.get("/download/{filename}")
def download_file(filename: str):
    return FileResponse(path=filename, filename=filename)