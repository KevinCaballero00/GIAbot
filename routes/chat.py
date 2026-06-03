import asyncio
import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from models.message import Message
from services.ai_service import generar_respuesta
from services.auth_service import verificar_credenciales
from services.complete_pdf import pdf_completer
from services.extractor_proyectos import (
    calcular_periodo,
    _obtener_docentes_cvlac,
    _normalizar_nombre,
)
from services.fo_in_13_service import generar_fo_in_13
from services.fo_in_17_service import generar_fo_in_17


logger = logging.getLogger(__name__)

router = APIRouter()

# ── Directorio y nombres oficiales de los PDFs generados ──────────────────────
GENERADOS_DIR = Path(__file__).resolve().parent.parent / "static" / "generados"
NOMBRES_OFICIALES = {
    "13": "FO-IN-13 INFORME GESTION GRUPOS INV V1.pdf",
    "17": "FO-IN-17 PLAN DE ACCION GRUPOS INV V1.pdf",
}

# Palabras a ignorar al detectar el nombre de un docente en el mensaje
_STOPWORDS_NOMBRE = {
    "de", "del", "la", "el", "los", "las", "grupo", "gia", "docente", "profe",
    "profesor", "profesora", "investigador", "investigadora", "proyectos",
    "proyecto", "informe", "plan", "accion", "gestion", "para", "con", "que",
    "trabajados", "trabajado", "por", "una", "uno",
}

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

# ── Alias para detectar intención de COMPLETAR (no solo descargar) ────────────
ALIASES_COMPLETAR = [
    "completar", "llenar", "rellenar", "diligenciar", "llena este", "completa este"
]

def detectar_intencion_completar(mensaje: str):
    """Detecta si el usuario quiere COMPLETAR un PDF en lugar de solo descargarlo.
    Retorna 13, 17 o None."""
    msg = mensaje.lower()
    
    # Debe tener una palabra de completado
    tiene_verbo_completar = any(v in msg for v in ALIASES_COMPLETAR)
    if not tiene_verbo_completar:
        return None
    
    # Detectar cuál PDF quiere completar
    if any(alias in msg for alias in ALIASES_13):
        return 13
    if any(alias in msg for alias in ALIASES_17):
        return 17
    
    return None

VERBOS_SOLICITUD = [
    "genera", "généra", "envía", "envia", "manda", "dame", "necesito",
    "quiero", "descarga", "obten", "obtén", "proporciona", "muéstrame",
    "muestrame", "pásamelo", "pasamelo", "ahora", "también", "tambien",
    "completar", "rellenar", "llenar", "completa", "rellena", "llena  "
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


def detectar_docente_solicitado(mensaje: str) -> dict | None:
    """
    Detecta si el mensaje menciona a un docente específico del GIA (ej. "los
    proyectos de Ana Gissele", "lo de Puerto"). Compara los tokens del mensaje
    contra la lista de docentes del /team/ (con CvLAC) y devuelve el que mejor
    coincida como {"nombre": ..., "cvlac_url": ...}, o None si no se menciona a
    nadie reconocible (en cuyo caso se usa el docente autenticado).
    """
    msg_norm = _normalizar_nombre(mensaje or "")
    if not msg_norm.strip():
        return None

    try:
        team = _obtener_docentes_cvlac()
    except Exception as exc:
        logger.warning("No se pudo obtener la lista de docentes para detección: %s", exc)
        return None

    mejor: dict | None = None
    mejor_hits = 0
    for nombre, url in team:
        tokens = {
            t for t in _normalizar_nombre(nombre).split()
            if len(t) >= 3 and t not in _STOPWORDS_NOMBRE
        }
        hits = sum(1 for t in tokens if re.search(rf"\b{re.escape(t)}\b", msg_norm))
        if hits > mejor_hits:
            mejor_hits = hits
            mejor = {"nombre": nombre, "cvlac_url": url}

    if mejor:
        logger.info("Docente solicitado detectado: %s (%d coincidencias)",
                    mejor["nombre"], mejor_hits)
    return mejor


async def construir_respuesta_con_extraccion(
    pdfs: list,
    docente: dict | None = None,
    docente_objetivo: dict | None = None,
) -> str:
    """
    Construye la respuesta para una solicitud de PDFs.

    FO-IN-17: documento fuente del semestre actual — extrae, persiste y genera PDF.
    FO-IN-13: documento derivado — usa el FO-IN-17 del semestre anterior como fuente.

    `docente_objetivo` (opcional): docente cuyos proyectos se piden realmente,
    detectado del mensaje (ej. "los proyectos de Ana Gissele").
    """
    semestre_actual, _, _ = calcular_periodo()
    partes: list[str] = []
    nota_objetivo = (
        f"\n👤 Proyectos atribuidos a: **{docente_objetivo['nombre']}**"
        if docente_objetivo else ""
    )

    if 17 in pdfs:
        try:
            resultado_17 = await asyncio.to_thread(
                generar_fo_in_17, docente, semestre_actual, docente_objetivo
            )
            nombre_17 = resultado_17["pdf_nombre"]
            advertencia = resultado_17.get("advertencia", "")
            bloque = (
                "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
                f"Semestre: {semestre_actual}{nota_objetivo}\n"
                f"👉 [Descargar informe (PDF)](/descargar/17/{nombre_17})"
            )
            if advertencia:
                bloque += f"\n⚠️ {advertencia}"
            partes.append(bloque)
        except Exception as exc:
            partes.append(
                "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
                f"⚠️ No se pudo generar el PDF: {exc}\n"
                "👉 [Descargar plantilla base](/static/docs/FO-IN-17%20PLAN%20DE%20ACCION%20GRUPOS%20INV%20V1.pdf)"
            )

    if 13 in pdfs:
        try:
            resultado_13 = await asyncio.to_thread(
                generar_fo_in_13, docente, semestre_actual, docente_objetivo
            )
            nombre_13 = resultado_13["pdf_nombre"]
            sem_ref = resultado_13["semestre_referencia"]
            partes.append(
                "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
                f"Generado a partir del Plan de Acción (FO-IN-17) del semestre {sem_ref}.{nota_objetivo}\n"
                f"👉 [Descargar informe (PDF)](/descargar/13/{nombre_13})"
            )
        except Exception as exc:
            partes.append(
                "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
                f"⚠️ No se pudo generar el informe: {exc}"
            )

    intro = (
        "Aquí tienes los documentos solicitados:"
        if len(pdfs) > 1
        else "Aquí tienes el documento solicitado:"
    )
    return intro + "\n\n" + "\n\n".join(partes)


@router.get("/descargar/{tipo}/{filename}")
async def descargar(tipo: str, filename: str):
    """
    Sirve un PDF generado con el NOMBRE OFICIAL de descarga.

    El archivo en disco tiene un nombre único interno, pero el navegador lo
    descargará como 'FO-IN-17 PLAN DE ACCION GRUPOS INV V1.pdf' (o FO-IN-13)
    gracias al Content-Disposition que fija FileResponse(filename=...).
    """
    nombre_disco = Path(filename).name  # evita path traversal
    ruta = GENERADOS_DIR / nombre_disco
    if not ruta.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    nombre_descarga = NOMBRES_OFICIALES.get(tipo, nombre_disco)
    return FileResponse(
        str(ruta),
        media_type="application/pdf",
        filename=nombre_descarga,
    )


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
                docente_obj = estado.get("docente_solicitado")
                respuesta_docs = await construir_respuesta_con_extraccion(pdfs, docente, docente_obj)
                return {
                    "reply": f"✅ Bienvenido/a, **{docente['nombre']}**. Acceso verificado.\n\n"
                             + respuesta_docs
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
        # Detectar si se pide un docente específico (Ana, Pardo, Puerto…)
        docente_solicitado = await asyncio.to_thread(detectar_docente_solicitado, mensaje)
        if autenticado:
            # Ya está autenticado, entrega directamente + extracción web
            return {"reply": await construir_respuesta_con_extraccion(
                pdfs_solicitados, estado["docente"], docente_solicitado)}
        else:
            # Iniciar flujo de autenticación
            sesiones_activas[session_id] = {
                "paso": "esperando_usuario",
                "pdfs_solicitados": pdfs_solicitados,
                "docente_solicitado": docente_solicitado,
                "usuario_ingresado": None,
                "autenticado": False,
                "docente": None,
            }
            return {
                "reply": "🔒 Para acceder a los documentos del semillero necesito verificar "
                         "tu identidad.\n\n👤 Por favor ingresa tu **usuario**:"
            }

    # ── NUEVO: Detectar intención de COMPLETAR PDF ────────────────────────────
    pdf_a_completar = detectar_intencion_completar(mensaje)
    
    if pdf_a_completar:
        if autenticado:
            # Iniciar flujo de completado
            sesiones_activas[session_id] = {
                "paso": "completando_pdf",
                "pdf_numero": pdf_a_completar,
                "datos_recolectados": {},
                "campo_actual": 0,
                "campos_pendientes": pdf_completer.PDF_CONFIG[pdf_a_completar]["campos"]
            }
            
            primer_campo = sesiones_activas[session_id]["campos_pendientes"][0]
            return {
                "reply": f"📝 Vamos a completar el **{pdf_completer.PDF_CONFIG[pdf_a_completar]['descripcion']}**.\n\n"
                         f"Por favor, dime el **{primer_campo.replace('_', ' ').title()}**:"
            }
        else:
            # Primero autenticar, luego completar
            sesiones_activas[session_id] = {
                "paso": "esperando_usuario",
                "pdfs_solicitados": [pdf_a_completar],
                "completar_despues": True,  # Flag para saber que después debe llenar
                "usuario_ingresado": None,
                "autenticado": False,
                "docente": None,
            }
            return {
                "reply": "🔒 Para acceder a los documentos necesito verificar tu identidad.\n\n"
                         "👤 Por favor ingresa tu **usuario**:"
            }
    
    # ── Flujo de completado de PDF en curso ───────────────────────────────────
    if estado and estado.get("paso") == "completando_pdf":
        campos = estado["campos_pendientes"]
        idx_actual = estado["campo_actual"]
        
        # Guardar el dato actual
        campo = campos[idx_actual]
        estado["datos_recolectados"][campo] = mensaje.strip()
        
        # Avanzar al siguiente campo
        if idx_actual + 1 < len(campos):
            estado["campo_actual"] += 1
            siguiente_campo = campos[estado["campo_actual"]]
            return {
                "reply": f"Gracias. Ahora, dime el **{siguiente_campo.replace('_', ' ').title()}**:"
            }
        else:
            # Todos los campos recolectados → generar PDF
            pdf_numero = estado["pdf_numero"]
            datos = estado["datos_recolectados"]
            
            # Generar el PDF completado
            try:
                output_path = pdf_completer.completar_pdf(pdf_numero, datos)
                nombre_archivo = os.path.basename(output_path)
                
                # Limpiar sesión
                del sesiones_activas[session_id]
                
                return {
                    "reply": f"✅ ¡PDF completado exitosamente!\n\n"
                             f"📄 Descarga tu documento aquí: [Descargar](/download/{nombre_archivo})"
                }
            except Exception as e:
                return {
                    "reply": f"❌ Ocurrió un error al generar el PDF: {str(e)}"
                }

    # ── Chat normal: delegar al modelo ────────────────────────────────────────
    respuesta = generar_respuesta(mensaje, data.history)
    return {"reply": respuesta}