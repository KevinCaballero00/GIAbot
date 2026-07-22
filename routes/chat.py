"""
Router principal de chat de GIAbot.

Maneja en orden de prioridad:
  1. Flujos de autenticación en curso (usuario/contraseña).
  2. Flujo de registro conversacional de proyectos en curso.
  3. Confirmación del FO-IN-17 fuente antes de generar el FO-IN-13.
  4. Flujo de % de cumplimiento del FO-IN-13 en curso.
  5. Flujo de recolección conversacional de las secciones 2/3/4 del FO-IN-17 en curso.
     Al arrancar, primero se pregunta el modo (paso a paso vs. subir un documento);
     ver "elegir_modo" en `_bloque_fo_in_17` / `_procesar_fase_elegir_modo`.
  6. Flujo del selector de informe existente vs. generar uno nuevo en curso.
  7. Flujo de subida de documento para las secciones 2/3/4 en curso
     (`esperando_documento_fo17` espera el archivo vía `POST /chat/documento`;
     `confirmando_documento_fo17` espera la confirmación sí/no del resumen extraído).
  8. Flujo de completado de PDF en curso.
  9. Detección de "actualizar plan de acción" (ANTES de detectar_pdf_solicitado:
     "plan de acción" ya es alias del 17 y entregaría el cacheado sin re-preguntar).
  10. Detección de solicitud de PDFs (FO-IN-13 / FO-IN-17): si el docente ya está
     autenticado y pide un único documento con reportes previos, se muestra el
     selector existente/nuevo en vez de generar directo (ver
     `_entregar_pdfs_o_selector`). Si pide "ambos" documentos a la vez, se
     mantiene el comportamiento directo (sin selector).
  11. Detección de intención de registrar proyecto.
  12. Detección de intención de completar PDF.
  13. Chat normal delegado al LLM.

Todas las interacciones quedan registradas en conversation_logs (incluida la
subida de documento, con intención "documento_fo_in_17").
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from models.message import Message
from services.ai_service import generar_respuesta
from services.auth_service import verificar_credenciales
from services.complete_pdf import pdf_completer
from services.estructurador import estructurar_secciones_desde_texto
from services.extractor_proyectos import RESPONSABLE_GRUPAL, calcular_periodo
from services.fo_in_13_service import generar_fo_in_13, obtener_fuente_fo_in_13
from services.fo_in_17_service import actualizar_datos_recolectados, generar_fo_in_17
from services.lector_documento import extraer_texto
from services.pdf_fo_in_13 import proyectos_validos
from services.validadores_fo_in_17 import parse_fecha as _parse_fecha, parse_nivel as _parse_nivel


logger = logging.getLogger(__name__)

router = APIRouter()

# ── Directorio y nombres oficiales de los PDFs generados ──────────────────────
GENERADOS_DIR = Path(__file__).resolve().parent.parent / "static" / "generados"
NOMBRES_OFICIALES = {
    "13": "FO-IN-13 INFORME GESTION GRUPOS INV V1.pdf",
    "17": "FO-IN-17 PLAN DE ACCION GRUPOS INV.pdf",
}

# ── Sesiones activas en memoria { session_id: {docente, estado} } ─────────────
sesiones_activas: dict = {}

# ── Alias de los PDFs ─────────────────────────────────────────────────────────
ALIASES_13 = [
    "fo-in-13", "fo in 13", "foin13", "informe 13", "número 13",
    "numero 13", "informe gestion", "gestión grupos", "gestion grupos",
    "informe de gestión", "informe de gestion",
]

ALIASES_17 = [
    "fo-in-17", "fo in 17", "foin17", "informe 17", "número 17",
    "numero 17", "plan de accion", "plan de acción", "plan accion",
    "plan acción",
]

# ── Alias para forzar la re-recolección de las secciones 2/3/4 del FO-IN-17 ──
ALIASES_ACTUALIZAR_17 = [
    "actualizar plan de accion", "actualizar plan de acción",
    "actualizar el plan de accion", "actualizar el plan de acción",
    "actualizar fo-in-17", "actualizar fo in 17", "actualizar el fo-in-17",
    "modificar plan de accion", "modificar plan de acción",
    "editar plan de accion", "editar plan de acción",
]

# ── Alias para completar PDF ──────────────────────────────────────────────────
ALIASES_COMPLETAR = [
    "completar", "llenar", "rellenar", "diligenciar", "llena este", "completa este",
]

# ── Alias para registrar proyecto ─────────────────────────────────────────────
ALIASES_REGISTRAR = [
    "registrar proyecto", "registrar un proyecto", "quiero registrar",
    "agregar proyecto", "añadir proyecto", "nuevo proyecto",
    "ingresar proyecto", "cargar proyecto", "registrar mi proyecto",
    "quiero agregar un proyecto", "cargar un nuevo proyecto",
]

# ── Campos del formulario de registro de proyecto ────────────────────────────
_CAMPOS_PROYECTO = [
    {
        "clave": "titulo",
        "pregunta": "¿Cuál es el **título** del proyecto?",
        "obligatorio": True,
        "default": None,
    },
    {
        "clave": "linea",
        "pregunta": "¿A qué **línea de investigación** pertenece?\n_(Ej: Sistemas Inteligentes Aplicados — deja vacío para usar esa por defecto)_",
        "obligatorio": False,
        "default": "Sistemas Inteligentes Aplicados",
    },
    {
        "clave": "objetivo",
        "pregunta": "Describe el **objetivo principal** del proyecto en 1–2 frases:",
        "obligatorio": False,
        "default": "",
    },
    {
        "clave": "actividades",
        "pregunta": "Lista las **actividades principales** separadas por comas\n_(Ej: Revisión bibliográfica, Diseño del modelo, Pruebas)_:",
        "obligatorio": False,
        "default": "",
    },
    {
        "clave": "producto",
        "pregunta": "¿Cuál es el **producto esperado**?\n_(artículo, ponencia, software, prototipo, informe…)_:",
        "obligatorio": False,
        "default": "",
    },
    {
        "clave": "periodo",
        "pregunta": "¿En qué **período académico**?\n_(deja vacío para usar el semestre actual)_",
        "obligatorio": False,
        "default": None,  # se resuelve en tiempo de ejecución
    },
]

# ── Campos de la recolección conversacional del FO-IN-17 (secciones 2/3/4) ───
_CAMPOS_TRABAJO = [
    {"clave": "titulo", "pregunta": "¿Cuál es el **título** del trabajo de grado?", "default": None},
    {"clave": "estudiante", "pregunta": "¿Cuál es el **nombre del estudiante**?", "default": None},
    {
        "clave": "director",
        "pregunta": "¿Quién es el **director**?\n_(deja vacío o escribe **yo** para usar tu nombre)_",
        "default": "__DOCENTE__",
    },
    {"clave": "programa", "pregunta": "¿Cuál es el **programa académico**?", "default": None},
    {
        "clave": "institucion",
        "pregunta": "¿Cuál es la **institución**?\n_(deja vacío para usar UFPS)_",
        "default": "Universidad Francisco de Paula Santander",
    },
    {
        "clave": "nivel",
        "pregunta": "¿Cuál es el **nivel**? (Pregrado / Especialización / Maestría / Doctorado)",
        "default": None,
        "validador": "nivel",
    },
]

_CAMPOS_EVENTO = [
    {"clave": "nombre", "pregunta": "¿Cuál es el **nombre del evento**?", "default": None},
    {
        "clave": "fecha",
        "pregunta": "¿Cuál es la **fecha de realización**?\n_(ej: 15/10/2026)_",
        "default": None,
        "validador": "fecha",
    },
    {
        "clave": "responsable",
        "pregunta": "¿Quién es el **responsable**?\n_(deja vacío para usar \"Miembros GIA\")_",
        "default": "Miembros GIA",
    },
    {
        "clave": "institucion_promotora",
        "pregunta": "¿Cuál es la **institución promotora**?\n_(deja vacío para usar UFPS)_",
        "default": "Universidad Francisco de Paula Santander",
    },
    {
        "clave": "entidades_participantes",
        "pregunta": "¿Qué **entidades participantes** hay?\n_(deja vacío si no aplica)_",
        "default": "",
    },
]

_FECHAS_OTRAS = [
    {"clave": "coordinacion_semillero", "pregunta": "¿Cuál es la **fecha** de la Coordinación del Semillero SIA?"},
    {"clave": "eventos_academicos", "pregunta": "¿Cuál es la **fecha** de Participación en Eventos Académicos?"},
    {"clave": "actualizaciones", "pregunta": "¿Cuál es la **fecha** de Actualizaciones (talleres/cursos/webinars)?"},
    {
        "clave": "reunion_mensual",
        "pregunta": (
            "¿Cuál es la **fecha** de la Reunión mensual de avances GIA?\n"
            "_(puedes escribir texto libre, ej: \"Último viernes de cada mes\")_"
        ),
    },
]

_PALABRAS_TERMINAR = [
    "ninguno", "ninguna", "no tengo", "listo", "omitir", "nada",
    "terminar", "terminé", "termine", "ya no",
]
_PALABRAS_OMITIR_TODO = [
    "omitir todo", "genera ya", "generar ya", "genéralo ya", "generalo ya",
    "termina todo", "así está bien", "asi esta bien",
]

VERBOS_SOLICITUD = [
    "genera", "généra", "envía", "envia", "manda", "dame", "necesito",
    "quiero", "descarga", "obten", "obtén", "proporciona", "muéstrame",
    "muestrame", "pásamelo", "pasamelo", "ahora", "también", "tambien",
    "completar", "rellenar", "llenar", "completa", "rellena", "llena  ",
]

# ── Palabras de confirmación / negación ───────────────────────────────────────

_AFIRMACIONES = [
    "si", "sí", "correcto", "prosigue", "continua", "continúa", "continuar",
    "adelante", "procede", "proceder", "dale", "ok", "okay", "listo", "exacto",
    "afirmativo", "claro", "por favor", "hazlo", "generalo", "genéralo",
    "generar", "acepto", "confirmado", "confirmo", "seguir", "sigue",
]

_NEGACIONES = [
    "no", "incorrecto", "negativo", "cancela", "cancelar", "detente",
    "espera", "no sigas", "no continues", "no continúes", "detener",
    "equivocado", "erróneo", "erroneo", "para", "parar",
]


def _es_afirmacion(mensaje: str) -> bool:
    msg = mensaje.lower().strip()
    return any(re.search(rf"\b{re.escape(a)}\b", msg) for a in _AFIRMACIONES)


def _es_negacion(mensaje: str) -> bool:
    msg = mensaje.lower().strip()
    return any(re.search(rf"\b{re.escape(n)}\b", msg) for n in _NEGACIONES)


def _formatear_fecha_display(fecha_iso: str | None) -> str:
    """Convierte un ISO datetime a 'DD de mes de YYYY' en español."""
    if not fecha_iso:
        return "fecha desconocida"
    meses = {
        1: "enero", 2: "febrero", 3: "marzo", 4: "abril",
        5: "mayo", 6: "junio", 7: "julio", 8: "agosto",
        9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre",
    }
    try:
        dt = datetime.fromisoformat(fecha_iso)
        return f"{dt.day} de {meses[dt.month]} de {dt.year}"
    except Exception:
        return fecha_iso[:10] if len(fecha_iso) >= 10 else fecha_iso


# ── Detectores de intención ───────────────────────────────────────────────────

def _parse_porcentaje(mensaje: str) -> str | None:
    """
    Extrae un porcentaje válido (0–100) del mensaje y lo normaliza a 'NN%'.
    Acepta '90', '90%', 'el 90 %', etc. Retorna None si no hay un valor válido.
    """
    m = re.search(r"\d{1,3}", mensaje or "")
    if not m:
        return None
    valor = int(m.group(0))
    if valor < 0 or valor > 100:
        return None
    return f"{valor}%"


def _es_terminar(msg_lower: str) -> bool:
    return any(p in msg_lower for p in _PALABRAS_TERMINAR)


def detectar_intencion_completar(mensaje: str) -> int | None:
    """Retorna 13, 17 o None."""
    msg = mensaje.lower()
    if not any(v in msg for v in ALIASES_COMPLETAR):
        return None
    if any(alias in msg for alias in ALIASES_13):
        return 13
    if any(alias in msg for alias in ALIASES_17):
        return 17
    return None


def detectar_intencion_registrar(mensaje: str) -> bool:
    """True si el mensaje indica que el usuario quiere registrar un proyecto."""
    msg = mensaje.lower()
    return any(alias in msg for alias in ALIASES_REGISTRAR)


def detectar_pdf_solicitado(mensaje: str, historial: list) -> list[int]:
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
        msg,
    )
    es_solicitud_pdf = tiene_verbo and tiene_referencia_pdf

    if not pide_13 and not pide_17 and not pide_ambos:
        ultimo_bot = next(
            (h["content"] for h in reversed(historial) if h["role"] == "assistant"),
            "",
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


# ── Construcción de respuesta con extracción de PDFs ────────────────────────

async def _bloque_fo_in_17(
    docente: dict | None,
    semestre_actual: str,
    session_id: str,
    forzar_recoleccion: bool = False,
) -> tuple[str, list[str], bool]:
    """
    Genera el FO-IN-17 y devuelve (bloque_markdown, fuentes, recoleccion_iniciada).

    Si los datos de las secciones 2/3/4 ya fueron recolectados (y no se pide
    `forzar_recoleccion`), entrega el link directo. En caso contrario configura
    la sesión para iniciar la recolección conversacional y retorna el mensaje
    de arranque en vez del link.
    """
    fuentes: list[str] = []
    try:
        resultado_17 = await asyncio.to_thread(
            generar_fo_in_17, docente, semestre_actual
        )
    except Exception as exc:
        return (
            "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
            f"⚠️ No se pudo generar el PDF: {exc}\n"
            "👉 [Descargar plantilla base](/static/docs/FO-IN-17%20PLAN%20DE%20ACCION%20GRUPOS%20INV%20V1.pdf)",
            fuentes,
            False,
        )

    nombre_17 = resultado_17["pdf_nombre"]
    advertencia = resultado_17.get("advertencia", "")
    datos = resultado_17.get("datos", {})
    fuentes.extend(datos.get("fuentes_consultadas", []))

    if datos.get("recoleccion_completada") and not forzar_recoleccion:
        bloque = (
            "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
            f"Semestre: {semestre_actual}\n"
            f"👉 [Descargar informe (PDF)](/descargar/17/{nombre_17})\n"
            "_Escribe **actualizar plan de acción** si deseas modificar los datos recolectados._"
        )
        if advertencia:
            bloque += f"\n⚠️ {advertencia}"
        _guardar_reporte_asinc(docente, "17", semestre_actual, nombre_17, fuentes, RESPONSABLE_GRUPAL)
        return bloque, fuentes, False

    sugerencias = datos.get("trabajos_grado_sugeridos") or []
    estado = sesiones_activas.setdefault(session_id, {})
    estado.update({
        "paso": "recolectando_fo_in_17",
        "autenticado": True,
        "docente": docente,
        "fo17_semestre": semestre_actual,
        "fo17_fase": "elegir_modo",
        "fo17_subfase": "inicio",
        "fo17_trabajos": [],
        "fo17_eventos": [],
        "fo17_fechas_otras": {},
        "fo17_item_actual": {},
        "fo17_campo_idx": 0,
        "fo17_sugerencias": sugerencias,
        "fo17_fecha_idx": 0,
        "fo17_intentos": 0,
    })

    bloque = (
        "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
        "Antes de entregarte el PDF necesito completar las secciones "
        "**2, 3 y 4** (trabajos de grado, eventos y otras actividades).\n\n"
        "Para eso, ¿prefieres…\n\n"
        "1. Digitar la información paso a paso\n"
        "2. Subir un documento (.txt, .docx o .pdf) con la información"
    )
    return bloque, fuentes, True


async def _bloque_fo_in_13(
    docente: dict | None,
    semestre_actual: str,
    datos_fuente: dict | None = None,
    sem_referencia: str | None = None,
    responsable_base: str | None = None,
    cumplimientos: dict | None = None,
) -> str:
    """Genera el FO-IN-13 (con % opcional) y devuelve el bloque markdown."""
    try:
        resultado_13 = await asyncio.to_thread(
            lambda: generar_fo_in_13(
                docente, semestre_actual,
                datos_fuente=datos_fuente, sem_referencia=sem_referencia,
                responsable_base=responsable_base,
                cumplimientos=cumplimientos,
            )
        )
        nombre_13 = resultado_13["pdf_nombre"]
        sem_ref = resultado_13["semestre_referencia"]
        responsable_base = resultado_13.get("responsable_base", "")
        _guardar_reporte_asinc(docente, "13", semestre_actual, nombre_13, [], responsable_base or None)
        nota_fuente = f" — basado en datos de **{responsable_base}**" if responsable_base else ""
        return (
            "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
            f"Generado a partir del FO-IN-17 del semestre {sem_ref}{nota_fuente}.\n"
            f"👉 [Descargar informe (PDF)](/descargar/13/{nombre_13})"
        )
    except Exception as exc:
        return (
            "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
            f"⚠️ No se pudo generar el informe: {exc}"
        )


def _nombre_corto(titulo: str | None, max_chars: int = 120) -> str:
    t = (titulo or "").strip()
    return t[:max_chars] + ("..." if len(t) > max_chars else "") if t else "este proyecto"


def _pregunta_cumplimiento(proyecto: dict) -> str:
    return f"Indique el porcentaje de cumplimiento del proyecto **{_nombre_corto(proyecto.get('proyecto'))}**"


async def iniciar_entrega_pdfs(
    session_id: str,
    pdfs: list,
    docente: dict | None,
    forzar_recoleccion_17: bool = False,
) -> tuple[str, list[str]]:
    """
    Orquesta la entrega de los PDFs solicitados.

    - FO-IN-17 se genera y entrega de inmediato, salvo que falten datos de las
      secciones 2/3/4 por recolectar (o se pida `forzar_recoleccion_17`), en
      cuyo caso se arranca el flujo conversacional (`recolectando_fo_in_17`).
    - FO-IN-13: primero muestra los metadatos del FO-IN-17 que se usará como
      fuente y pide confirmación (estado `confirmando_fo_in_13`). Cuando el
      docente confirma, `_procesar_confirmacion_fo_in_13` inicia el flujo de
      % de cumplimiento o genera directo si no hay proyectos.
    - Si el FO-IN-17 inició su propia recolección y también se pidió el 13,
      el 13 se encola (`fo17_pdfs_restantes`) para no correr dos máquinas de
      estado conversacionales a la vez; se retoma al terminar la del 17.

    Retorna (texto_respuesta, fuentes).
    """
    semestre_actual, _, _ = calcular_periodo()
    partes: list[str] = []
    fuentes: list[str] = []
    inicio_preguntas = False
    recoleccion_17_iniciada = False

    if 17 in pdfs:
        bloque_17, fuentes_17, recoleccion_17_iniciada = await _bloque_fo_in_17(
            docente, semestre_actual, session_id, forzar_recoleccion=forzar_recoleccion_17,
        )
        partes.append(bloque_17)
        fuentes.extend(fuentes_17)

    if 13 in pdfs and recoleccion_17_iniciada:
        estado_ses = sesiones_activas.setdefault(session_id, {})
        estado_ses["fo17_pdfs_restantes"] = [13]
    elif 13 in pdfs:
        try:
            fuente = await asyncio.to_thread(
                obtener_fuente_fo_in_13, docente, semestre_actual
            )
            datos_fuente = fuente["datos_fuente"]
            sem_ref = fuente["sem_referencia"]
            responsable_base = fuente.get("responsable_base", "")
            fo_in_17_fecha = fuente.get("fo_in_17_fecha", "")
            fo_in_17_generado_por = fuente.get("fo_in_17_generado_por", "")
        except Exception as exc:
            partes.append(
                "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
                f"⚠️ No se pudo preparar el informe: {exc}"
            )
            datos_fuente = None
            sem_ref = None
            responsable_base = ""
            fo_in_17_fecha = ""
            fo_in_17_generado_por = ""

        if datos_fuente is not None:
            # Guardar datos en sesión y mostrar confirmación antes de continuar.
            estado_ses = sesiones_activas.setdefault(session_id, {})
            estado_ses.update({
                "paso": "confirmando_fo_in_13",
                "autenticado": True,
                "docente": docente,
                "confirmacion_datos_fuente": datos_fuente,
                "confirmacion_sem_referencia": sem_ref,
                "confirmacion_responsable_base": responsable_base,
            })
            lineas_info: list[str] = []
            if responsable_base:
                lineas_info.append(f"📋 **Responsable:** {responsable_base}")
            lineas_info.append(f"📚 **Semestre:** {sem_ref}")
            if fo_in_17_fecha:
                lineas_info.append(f"📅 **Fecha de generación:** {_formatear_fecha_display(fo_in_17_fecha)}")
            if fo_in_17_generado_por:
                lineas_info.append(f"👤 **Generado por:** {fo_in_17_generado_por}")
            msg_confirmacion = (
                "El siguiente **FO-IN-13 Informe de Gestión de Grupos de Investigación** "
                "se realizará a partir del FO-IN-17:\n\n"
                + "\n".join(lineas_info)
                + "\n\n¿Desea continuar?"
            )
            partes.append(msg_confirmacion)
            inicio_preguntas = True

    if recoleccion_17_iniciada:
        # El FO-IN-17 aún no se entrega: se inició su propia recolección conversacional.
        intro = ""
    elif inicio_preguntas:
        # Aún no se entrega el FO-IN-13; se inician las preguntas de %.
        intro = "Aquí tienes el FO-IN-17 solicitado:" if 17 in pdfs else ""
    else:
        intro = (
            "Aquí tienes los documentos solicitados:"
            if len(pdfs) > 1
            else "Aquí tienes el documento solicitado:"
        )
    return (intro + "\n\n" + "\n\n".join(partes)).strip(), fuentes


# ── Selector de informe existente vs. generar uno nuevo ─────────────────────

_NOMBRES_DOC = {"13": "FO-IN-13", "17": "FO-IN-17"}


def _construir_lista_reportes(
    tipo: str, session_id: str, docente: dict | None, reportes: list[dict],
) -> str:
    """
    Arma el mensaje numerado con los reportes ya generados de `tipo` más la
    opción de generar uno nuevo. Guarda la lista en la sesión y fija
    paso="seleccionando_reporte" para que el próximo mensaje se despache a
    `_procesar_seleccion_reporte`.
    """
    estado = sesiones_activas.setdefault(session_id, {})
    estado.update({
        "paso": "seleccionando_reporte",
        "autenticado": True,
        "docente": docente,
        "sel_reportes": reportes,
        "sel_tipo": tipo,
    })

    nombre_doc = _NOMBRES_DOC.get(tipo, tipo)
    lineas = [f"¿Qué {nombre_doc} deseas?", ""]
    for i, r in enumerate(reportes, 1):
        fecha = _formatear_fecha_display(r.get("fecha_generacion"))
        lineas.append(f"{i}. {nombre_doc} — creado el {fecha} · semestre {r.get('semestre')}")
    lineas.append(f"{len(reportes) + 1}. ➕ Generar uno nuevo")
    return "\n".join(lineas)


async def _entregar_pdfs_o_selector(
    session_id: str,
    pdfs: list[int],
    docente: dict | None,
    forzar_recoleccion_17: bool = False,
) -> tuple[str, list[str]]:
    """
    Punto de entrada común para entregar PDFs a un docente autenticado.

    Si se solicita un único tipo y ya existen reportes previos de ese tipo,
    muestra el selector (existente/nuevo) en vez de generar directo. Alcance
    MVP: si se piden ambos documentos a la vez se mantiene el comportamiento
    directo actual (no hay selector para "ambos").
    """
    from services.rag_service import listar_reportes

    if len(pdfs) == 1 and not forzar_recoleccion_17:
        tipo = str(pdfs[0])
        reportes = listar_reportes(tipo)
        if reportes:
            return _construir_lista_reportes(tipo, session_id, docente, reportes), []

    return await iniciar_entrega_pdfs(
        session_id, pdfs, docente, forzar_recoleccion_17=forzar_recoleccion_17,
    )


async def _procesar_seleccion_reporte(session_id: str, mensaje: str) -> str:
    """
    Procesa la respuesta del docente al selector de informe existente/nuevo.

    Número de un reporte existente → re-entrega el enlace (verificando que el
    archivo siga existiendo en disco). Opción "generar uno nuevo" (último
    número de la lista, o las palabras "nuevo"/"otro") → continúa con el flujo
    normal de generación. Entrada no reconocida → repite la lista.
    """
    estado = sesiones_activas[session_id]
    reportes: list[dict] = estado.get("sel_reportes", [])
    tipo = estado.get("sel_tipo")
    docente = estado.get("docente")
    msg = mensaje.strip().lower()
    opcion_nuevo = len(reportes) + 1

    def _limpiar_seleccion() -> None:
        estado["paso"] = "autenticado"
        estado.pop("sel_reportes", None)
        estado.pop("sel_tipo", None)

    pide_nuevo = msg == str(opcion_nuevo) or any(p in msg for p in ("nuevo", "otro", "generar"))

    if msg.isdigit() and not pide_nuevo and 1 <= int(msg) <= len(reportes):
        reporte = reportes[int(msg) - 1]
        nombre_disco = Path(reporte["pdf_path"]).name
        ruta = GENERADOS_DIR / nombre_disco
        _limpiar_seleccion()
        if not ruta.exists():
            return (
                "⚠️ Ese archivo ya no está disponible. Vuelve a pedir el documento "
                "para generar uno nuevo."
            )
        nombre_doc = _NOMBRES_DOC.get(tipo, tipo)
        fecha = _formatear_fecha_display(reporte.get("fecha_generacion"))
        return (
            f"📄 **{nombre_doc}**\n"
            f"Semestre: {reporte.get('semestre')} · Generado el {fecha}\n"
            f"👉 [Descargar informe (PDF)](/descargar/{tipo}/{nombre_disco})"
        )

    if pide_nuevo:
        _limpiar_seleccion()
        respuesta, _ = await iniciar_entrega_pdfs(session_id, [int(tipo)], docente)
        return respuesta

    return (
        "No reconocí esa opción. Responde con el número de un informe de la lista "
        f"o **{opcion_nuevo}** para generar uno nuevo."
    )


async def _continuar_generacion_fo_in_13(
    session_id: str,
    docente: dict | None,
    datos_fuente: dict,
    sem_ref: str,
    responsable_base: str,
    semestre_actual: str,
) -> str:
    """
    Inicia el flujo de % de cumplimiento si hay proyectos, o genera el
    FO-IN-13 directamente si no los hay. Se llama tras la confirmación del usuario.
    """
    proyectos = proyectos_validos(datos_fuente.get("proyectos", []))

    if proyectos:
        estado = sesiones_activas.setdefault(session_id, {})
        estado.update({
            "paso": "recolectando_cumplimiento",
            "autenticado": True,
            "docente": docente,
            "cumpl_proyectos": proyectos,
            "cumpl_idx": 0,
            "cumpl_valores": {},
            "cumpl_datos_fuente": datos_fuente,
            "cumpl_sem_referencia": sem_ref,
            "cumpl_responsable_base": responsable_base,
        })
        return (
            f"Para generar el **FO-IN-13** necesito el porcentaje de cumplimiento "
            f"de cada uno de los {len(proyectos)} proyectos del Plan de Acción "
            f"(FO-IN-17) del semestre {sem_ref}.\n\n"
            + _pregunta_cumplimiento(proyectos[0])
        )

    return await _bloque_fo_in_13(
        docente, semestre_actual,
        datos_fuente=datos_fuente, sem_referencia=sem_ref,
        responsable_base=responsable_base, cumplimientos={},
    )


async def _procesar_confirmacion_fo_in_13(session_id: str, mensaje: str) -> str:
    """
    Procesa la respuesta del docente al mensaje de confirmación del FO-IN-17 fuente.
    Afirmación → inicia la generación. Negación → cancela.
    """
    estado = sesiones_activas[session_id]
    semestre_actual, _, _ = calcular_periodo()

    if _es_negacion(mensaje) and not _es_afirmacion(mensaje):
        estado["paso"] = "autenticado"
        for clave in (
            "confirmacion_datos_fuente", "confirmacion_sem_referencia",
            "confirmacion_responsable_base",
        ):
            estado.pop(clave, None)
        return (
            "❌ Generación del **FO-IN-13** cancelada. "
            "Si deseas usar otro FO-IN-17 como base, primero genera ese documento y luego pide el FO-IN-13."
        )

    if _es_afirmacion(mensaje):
        datos_fuente = estado.pop("confirmacion_datos_fuente", {})
        sem_ref = estado.pop("confirmacion_sem_referencia", "")
        responsable_base = estado.pop("confirmacion_responsable_base", "")
        docente = estado.get("docente")
        estado["paso"] = "autenticado"

        return await _continuar_generacion_fo_in_13(
            session_id, docente,
            datos_fuente, sem_ref, responsable_base, semestre_actual,
        )

    return (
        "Por favor responde **sí** para continuar con el FO-IN-17 mostrado "
        "o **no** para cancelar."
    )


async def _procesar_cumplimiento(session_id: str, mensaje: str) -> str:
    """Procesa cada respuesta del flujo de % de cumplimiento del FO-IN-13."""
    estado = sesiones_activas[session_id]
    proyectos = estado["cumpl_proyectos"]
    idx = estado["cumpl_idx"]
    proyecto_actual = proyectos[idx]
    titulo = (proyecto_actual.get("proyecto") or "").strip()

    pct = _parse_porcentaje(mensaje)
    if pct is None:
        return (
            "Por favor indica un porcentaje entre 0 y 100 (por ejemplo: **90%**) para el "
            f"proyecto **{_nombre_corto(titulo)}**."
        )

    # Clave por posición (no por título): los títulos pueden repetirse o venir vacíos.
    estado["cumpl_valores"][idx] = pct
    estado["cumpl_idx"] = idx + 1

    # ¿Quedan más proyectos por preguntar?
    if estado["cumpl_idx"] < len(proyectos):
        siguiente = proyectos[estado["cumpl_idx"]]
        return f"✅ {pct} registrado.\n\n{_pregunta_cumplimiento(siguiente)}"

    # Todos respondidos → generar el FO-IN-13 con los porcentajes.
    semestre_actual, _, _ = calcular_periodo()
    docente = estado.get("docente")
    bloque = await _bloque_fo_in_13(
        docente,
        semestre_actual,
        datos_fuente=estado.get("cumpl_datos_fuente"),
        sem_referencia=estado.get("cumpl_sem_referencia"),
        responsable_base=estado.get("cumpl_responsable_base"),
        cumplimientos=estado.get("cumpl_valores", {}),
    )

    # Limpiar el estado conversacional y volver a "autenticado".
    estado["paso"] = "autenticado"
    for clave in (
        "cumpl_proyectos", "cumpl_idx", "cumpl_valores",
        "cumpl_datos_fuente", "cumpl_sem_referencia", "cumpl_responsable_base",
    ):
        estado.pop(clave, None)

    return "✅ ¡Listo! Generé tu informe con los porcentajes indicados.\n\n" + bloque


# ── Flujo de recolección conversacional del FO-IN-17 (secciones 2/3/4) ──────

def _resolver_valor_campo(
    campo: dict, msg: str, docente: dict | None, estado: dict,
) -> tuple[str, str | None]:
    """
    Resuelve el valor final de un campo (_CAMPOS_TRABAJO / _CAMPOS_EVENTO)
    aplicando default y validador. Retorna (valor, mensaje_error); si
    mensaje_error no es None, no se debe avanzar de campo.
    """
    valor = msg.strip()
    default = campo.get("default")

    if not valor:
        if default == "__DOCENTE__":
            return (docente.get("nombre") if docente else ""), None
        return (default or ""), None

    if default == "__DOCENTE__" and valor.lower() in ("yo", "yo mismo"):
        return (docente.get("nombre") if docente else ""), None

    validador = campo.get("validador")
    if validador == "nivel":
        nivel = _parse_nivel(valor)
        if nivel is None:
            return "", (
                "No reconocí ese nivel. Responde con una de estas opciones: "
                "**Pregrado**, **Especialización**, **Maestría** o **Doctorado**."
            )
        return nivel, None

    if validador == "fecha":
        intentos = estado.get("fo17_intentos", 0)
        fecha = _parse_fecha(valor)
        if fecha is None:
            if intentos >= 1:
                estado["fo17_intentos"] = 0
                return valor, None
            estado["fo17_intentos"] = intentos + 1
            return "", (
                "No reconocí esa fecha. Usa el formato **DD/MM/AAAA** (ej: 15/10/2026), "
                "o vuelve a escribirla y la aceptaré tal cual la escribas."
            )
        estado["fo17_intentos"] = 0
        return fecha, None

    return valor, None


def _pasar_a_fase_eventos(estado: dict) -> str:
    estado["fo17_fase"] = "eventos"
    estado["fo17_subfase"] = "inicio"
    estado["fo17_intentos"] = 0
    return (
        "Ahora la sección **3. Organización de Eventos de Investigación/Científicos**.\n\n"
        "¿Cuál es el **nombre** del primer evento? "
        "_(escribe **ninguno** o **listo** si no hay ninguno)_"
    )


def _pasar_a_fase_fechas_otras(estado: dict) -> str:
    estado["fo17_fase"] = "fechas_otras"
    estado["fo17_fecha_idx"] = 0
    estado["fo17_intentos"] = 0
    primera = _FECHAS_OTRAS[0]
    return (
        "Por último, la sección **4. Otras Actividades de Investigación**.\n\n"
        + primera["pregunta"] + "\n_(escribe **omitir** para dejarla vacía)_"
    )


# ── Elección de modo: paso a paso vs. subir documento ───────────────────────

def _es_modo_documento(msg_lower: str) -> bool:
    return msg_lower.strip() == "2" or any(
        p in msg_lower for p in ("documento", "subir", "cargar archivo", "adjuntar")
    )


def _es_modo_paso_a_paso(msg_lower: str) -> bool:
    return msg_lower.strip() == "1" or any(
        p in msg_lower for p in ("paso a paso", "digitar", "manual")
    )


def _texto_inicio_fase_trabajos(sugerencias: list[dict]) -> str:
    bloque = (
        "Empecemos con la sección **2. Participación en Dirección de** "
        "(trabajos de grado dirigidos).\n\n"
    )
    if sugerencias:
        lineas_sug = "\n".join(
            f"{i}. {s.get('titulo') or '(sin título)'} — {s.get('estudiante') or '(sin estudiante)'}"
            for i, s in enumerate(sugerencias, 1)
        )
        bloque += (
            "Encontré estas sugerencias en el CvLAC (confírmalas escribiendo su número, "
            "o dime el título de un trabajo distinto):\n\n" + lineas_sug + "\n\n"
        )
    bloque += (
        "¿Cuál es el **título** del primer trabajo de grado dirigido? "
        "_(escribe **ninguno** o **listo** si no hay ninguno)_"
    )
    return bloque


async def _procesar_fase_elegir_modo(session_id: str, mensaje: str) -> str:
    estado = sesiones_activas[session_id]
    msg_lower = mensaje.strip().lower()

    if _es_modo_documento(msg_lower):
        estado["paso"] = "esperando_documento_fo17"
        return (
            "📎 Adjunta un archivo **.txt, .docx o .pdf** con la información de trabajos de "
            "grado, eventos y otras actividades (usa el botón de adjuntar debajo del chat).\n\n"
            "_Si prefieres hacerlo paso a paso, escribe **paso a paso**._"
        )

    if _es_modo_paso_a_paso(msg_lower):
        estado["fo17_fase"] = "trabajos"
        estado["fo17_subfase"] = "inicio"
        return _texto_inicio_fase_trabajos(estado.get("fo17_sugerencias") or [])

    return (
        "No reconocí tu elección. Responde **1** para digitar la información paso a paso, "
        "o **2** para subir un documento."
    )


async def _procesar_fase_trabajos(session_id: str, mensaje: str) -> str:
    estado = sesiones_activas[session_id]
    msg = mensaje.strip()
    msg_lower = msg.lower()
    docente = estado.get("docente")

    if estado["fo17_subfase"] == "inicio":
        if _es_terminar(msg_lower):
            return _pasar_a_fase_eventos(estado)

        sugerencias = estado.get("fo17_sugerencias") or []
        if msg.isdigit() and sugerencias:
            n = int(msg)
            if 1 <= n <= len(sugerencias):
                sug = sugerencias[n - 1]
                trabajo = {
                    "titulo": sug.get("titulo", ""),
                    "estudiante": sug.get("estudiante", ""),
                    "director": sug.get("director") or (docente.get("nombre") if docente else ""),
                    "programa": sug.get("programa", ""),
                    "institucion": sug.get("institucion") or "Universidad Francisco de Paula Santander",
                    "nivel": _parse_nivel(sug.get("nivel", "")) or "Pregrado",
                }
                estado["fo17_trabajos"].append(trabajo)
                if len(estado["fo17_trabajos"]) >= 6:
                    return (
                        f"✅ Trabajo agregado: **{trabajo['titulo']}**.\n"
                        "Se alcanzó el máximo de 6 trabajos de grado.\n\n"
                        + _pasar_a_fase_eventos(estado)
                    )
                return (
                    f"✅ Trabajo agregado: **{trabajo['titulo']}**.\n\n"
                    "¿Cuál es el título del siguiente trabajo? "
                    "_(escribe **ninguno** o **listo** para continuar)_"
                )
            return (
                "Ese número no corresponde a ninguna sugerencia. Escribe el título del "
                "trabajo o **ninguno**/**listo** para continuar."
            )

        estado["fo17_item_actual"] = {"titulo": msg}
        estado["fo17_campo_idx"] = 1
        estado["fo17_subfase"] = "campos"
        estado["fo17_intentos"] = 0
        return _CAMPOS_TRABAJO[1]["pregunta"]

    idx = estado["fo17_campo_idx"]
    campo = _CAMPOS_TRABAJO[idx]
    valor_final, error = _resolver_valor_campo(campo, msg, docente, estado)
    if error:
        return error

    estado["fo17_item_actual"][campo["clave"]] = valor_final
    siguiente_idx = idx + 1
    if siguiente_idx < len(_CAMPOS_TRABAJO):
        estado["fo17_campo_idx"] = siguiente_idx
        estado["fo17_intentos"] = 0
        return _CAMPOS_TRABAJO[siguiente_idx]["pregunta"]

    trabajo = estado["fo17_item_actual"]
    estado["fo17_trabajos"].append(trabajo)
    estado["fo17_item_actual"] = {}
    estado["fo17_subfase"] = "inicio"

    if len(estado["fo17_trabajos"]) >= 6:
        return (
            f"✅ Trabajo agregado: **{trabajo['titulo']}**.\n"
            "Se alcanzó el máximo de 6 trabajos de grado.\n\n"
            + _pasar_a_fase_eventos(estado)
        )

    return (
        f"✅ Trabajo agregado: **{trabajo['titulo']}**.\n\n"
        "¿Cuál es el título del siguiente trabajo? "
        "_(escribe **ninguno** o **listo** para continuar)_"
    )


async def _procesar_fase_eventos(session_id: str, mensaje: str) -> str:
    estado = sesiones_activas[session_id]
    msg = mensaje.strip()
    msg_lower = msg.lower()
    docente = estado.get("docente")

    if estado["fo17_subfase"] == "inicio":
        if _es_terminar(msg_lower):
            return _pasar_a_fase_fechas_otras(estado)

        estado["fo17_item_actual"] = {"nombre": msg}
        estado["fo17_campo_idx"] = 1
        estado["fo17_subfase"] = "campos"
        estado["fo17_intentos"] = 0
        return _CAMPOS_EVENTO[1]["pregunta"]

    idx = estado["fo17_campo_idx"]
    campo = _CAMPOS_EVENTO[idx]
    valor_final, error = _resolver_valor_campo(campo, msg, docente, estado)
    if error:
        return error

    estado["fo17_item_actual"][campo["clave"]] = valor_final
    siguiente_idx = idx + 1
    if siguiente_idx < len(_CAMPOS_EVENTO):
        estado["fo17_campo_idx"] = siguiente_idx
        estado["fo17_intentos"] = 0
        return _CAMPOS_EVENTO[siguiente_idx]["pregunta"]

    evento = estado["fo17_item_actual"]
    estado["fo17_eventos"].append(evento)
    estado["fo17_item_actual"] = {}
    estado["fo17_subfase"] = "inicio"

    if len(estado["fo17_eventos"]) >= 4:
        return (
            f"✅ Evento agregado: **{evento['nombre']}**.\n"
            "Se alcanzó el máximo de 4 eventos.\n\n"
            + _pasar_a_fase_fechas_otras(estado)
        )

    return (
        f"✅ Evento agregado: **{evento['nombre']}**.\n\n"
        "¿Cuál es el nombre del siguiente evento? "
        "_(escribe **ninguno** o **listo** para continuar)_"
    )


async def _procesar_fase_fechas_otras(session_id: str, mensaje: str) -> str:
    estado = sesiones_activas[session_id]
    idx = estado["fo17_fecha_idx"]
    actividad = _FECHAS_OTRAS[idx]
    msg = mensaje.strip()
    msg_lower = msg.lower()

    if msg_lower in ("omitir", "ninguno", "ninguna", "no"):
        valor = ""
    else:
        intentos = estado.get("fo17_intentos", 0)
        fecha = _parse_fecha(msg)
        if fecha is None:
            if intentos >= 1:
                valor = msg
                estado["fo17_intentos"] = 0
            else:
                estado["fo17_intentos"] = intentos + 1
                return (
                    "No reconocí esa fecha. Usa el formato **DD/MM/AAAA**, escribe **omitir** "
                    "para dejarla vacía, o vuelve a escribirla tal cual y la aceptaré como texto."
                )
        else:
            valor = fecha
            estado["fo17_intentos"] = 0

    estado["fo17_fechas_otras"][actividad["clave"]] = valor
    siguiente_idx = idx + 1
    if siguiente_idx < len(_FECHAS_OTRAS):
        estado["fo17_fecha_idx"] = siguiente_idx
        siguiente = _FECHAS_OTRAS[siguiente_idx]
        return f"✅ Registrado.\n\n{siguiente['pregunta']}\n_(escribe **omitir** para dejarla vacía)_"

    return await _finalizar_recoleccion_fo_in_17(session_id)


async def _finalizar_recoleccion_fo_in_17(session_id: str) -> str:
    """Persiste lo recolectado, regenera el PDF y retoma el FO-IN-13 si estaba encolado."""
    estado = sesiones_activas[session_id]
    docente = estado.get("docente")
    semestre = estado.get("fo17_semestre") or calcular_periodo()[0]
    trabajos = estado.get("fo17_trabajos", [])
    eventos = estado.get("fo17_eventos", [])
    fechas_otras = estado.get("fo17_fechas_otras", {})
    pdfs_restantes = estado.get("fo17_pdfs_restantes", [])

    try:
        resultado = await asyncio.to_thread(
            actualizar_datos_recolectados, docente, semestre, trabajos, eventos, fechas_otras,
        )
        nombre_17 = resultado["pdf_nombre"]
        fuentes = resultado.get("datos", {}).get("fuentes_consultadas", [])
        _guardar_reporte_asinc(docente, "17", semestre, nombre_17, fuentes, RESPONSABLE_GRUPAL)
        bloque = (
            "✅ ¡Listo! Registré la información y regeneré el Plan de Acción.\n\n"
            f"👉 [Descargar informe (PDF)](/descargar/17/{nombre_17})"
        )
    except Exception as exc:
        bloque = f"❌ No se pudo regenerar el FO-IN-17 con los datos recolectados: {exc}"

    for clave in (
        "fo17_fase", "fo17_subfase", "fo17_trabajos", "fo17_eventos",
        "fo17_fechas_otras", "fo17_item_actual", "fo17_campo_idx",
        "fo17_sugerencias", "fo17_fecha_idx", "fo17_intentos",
        "fo17_semestre", "fo17_pdfs_restantes",
    ):
        estado.pop(clave, None)
    estado["paso"] = "autenticado"

    if 13 in pdfs_restantes:
        bloque_13, _ = await iniciar_entrega_pdfs(session_id, [13], docente)
        bloque += "\n\n" + bloque_13

    return bloque


async def _procesar_recoleccion_fo_in_17(session_id: str, mensaje: str) -> str:
    """Despacha el mensaje a la fase activa de la recolección (trabajos/eventos/fechas)."""
    estado = sesiones_activas[session_id]
    msg_lower = mensaje.strip().lower()

    if any(p in msg_lower for p in _PALABRAS_OMITIR_TODO):
        return await _finalizar_recoleccion_fo_in_17(session_id)

    fase = estado.get("fo17_fase")
    if fase == "elegir_modo":
        return await _procesar_fase_elegir_modo(session_id, mensaje)
    if fase == "trabajos":
        return await _procesar_fase_trabajos(session_id, mensaje)
    if fase == "eventos":
        return await _procesar_fase_eventos(session_id, mensaje)
    if fase == "fechas_otras":
        return await _procesar_fase_fechas_otras(session_id, mensaje)

    estado["paso"] = "autenticado"
    return "❌ Ocurrió un error en la recolección. Por favor vuelve a solicitar el plan de acción."


# ── Flujo de subida de documento para las secciones 2/3/4 del FO-IN-17 ──────

async def _procesar_esperando_documento(session_id: str, mensaje: str) -> str:
    """
    Mensaje de texto recibido mientras se espera el archivo adjunto: recuerda
    cómo adjuntarlo, u ofrece volver al flujo paso a paso si el docente lo pide.
    """
    estado = sesiones_activas[session_id]
    msg_lower = mensaje.strip().lower()

    if _es_modo_paso_a_paso(msg_lower):
        estado["paso"] = "recolectando_fo_in_17"
        estado["fo17_fase"] = "trabajos"
        estado["fo17_subfase"] = "inicio"
        return _texto_inicio_fase_trabajos(estado.get("fo17_sugerencias") or [])

    return (
        "Aún no he recibido ningún archivo. Usa el botón 📎 para adjuntar un "
        "**.txt, .docx o .pdf**, o escribe **paso a paso** para digitar la información "
        "directamente en el chat."
    )


async def _procesar_confirmacion_documento(session_id: str, mensaje: str) -> str:
    """
    Procesa la confirmación (sí/no) del resumen extraído del documento subido.
    Afirmación → fusiona lo detectado y regenera el PDF (reutiliza
    `_finalizar_recoleccion_fo_in_17`). Negación → descarta y vuelve a
    preguntar el modo.
    """
    estado = sesiones_activas[session_id]

    if _es_negacion(mensaje) and not _es_afirmacion(mensaje):
        estado.pop("doc_parsed", None)
        estado["paso"] = "recolectando_fo_in_17"
        estado["fo17_fase"] = "elegir_modo"
        return (
            "❌ Descarté la información del documento.\n\n"
            "Para completar las secciones 2, 3 y 4, ¿prefieres…\n\n"
            "1. Digitar la información paso a paso\n"
            "2. Subir otro documento (.txt, .docx o .pdf)"
        )

    if _es_afirmacion(mensaje):
        doc_parsed = estado.pop("doc_parsed", {})
        docente = estado.get("docente")
        nombre_docente = (docente.get("nombre") if docente else "") or ""

        trabajos = doc_parsed.get("trabajos_grado", [])
        for trabajo in trabajos:
            if not trabajo.get("director"):
                trabajo["director"] = nombre_docente

        estado["fo17_trabajos"] = trabajos
        estado["fo17_eventos"] = doc_parsed.get("eventos", [])
        estado["fo17_fechas_otras"] = doc_parsed.get("fechas_otras_actividades", {})
        return await _finalizar_recoleccion_fo_in_17(session_id)

    return "Por favor responde **sí** para usar esta información o **no** para descartarla."


def _guardar_reporte_asinc(docente, tipo, semestre, pdf_nombre, fuentes, responsable_nombre=None):
    """Guarda el registro del reporte generado sin bloquear la respuesta."""
    try:
        import json
        from services.rag_service import guardar_reporte
        docente_id = docente["id"] if docente else None
        responsable_nombre = responsable_nombre or (docente["nombre"] if docente else None)
        guardar_reporte(
            docente_id=docente_id,
            tipo=tipo,
            semestre=semestre,
            pdf_path=f"/static/generados/{pdf_nombre}",
            fuentes_usadas=json.dumps(fuentes, ensure_ascii=False),
            responsable_nombre=responsable_nombre,
            generado_por_docente_id=docente_id,
        )
    except Exception as exc:
        logger.debug("No se pudo guardar registro de reporte: %s", exc)


# ── Flujo de registro de proyecto ────────────────────────────────────────────

def _iniciar_registro_proyecto(session_id: str, docente: dict, semestre_actual: str) -> str:
    """Configura la sesión para el flujo de registro y devuelve el primer mensaje."""
    sesiones_activas[session_id]["paso"] = "registrando_proyecto"
    sesiones_activas[session_id]["campo_actual"] = 0
    sesiones_activas[session_id]["datos_proyecto"] = {}
    sesiones_activas[session_id]["semestre_registro"] = semestre_actual
    primer_campo = _CAMPOS_PROYECTO[0]
    return (
        f"📋 Vamos a registrar un nuevo proyecto.\n\n"
        f"{primer_campo['pregunta']}"
    )


async def _procesar_registro_proyecto(session_id: str, mensaje: str) -> str:
    """Procesa cada paso del flujo de registro y retorna la respuesta."""
    estado = sesiones_activas[session_id]
    idx = estado["campo_actual"]
    datos = estado["datos_proyecto"]
    campo_def = _CAMPOS_PROYECTO[idx]

    valor = mensaje.strip()
    if not valor and campo_def["default"] is not None:
        # Usar default
        default = campo_def["default"]
        if campo_def["clave"] == "periodo" and default is None:
            default, _, _ = calcular_periodo()
        valor = default or ""
    datos[campo_def["clave"]] = valor

    siguiente_idx = idx + 1
    if siguiente_idx < len(_CAMPOS_PROYECTO):
        estado["campo_actual"] = siguiente_idx
        sig = _CAMPOS_PROYECTO[siguiente_idx]
        return f"✅ Guardado.\n\n{sig['pregunta']}"

    # Todos los campos recolectados → persistir
    semestre, _, _ = calcular_periodo()
    periodo = datos.get("periodo") or semestre
    docente = estado.get("docente")
    docente_id = docente["id"] if docente else None
    responsable = datos.get("responsable") or (docente["nombre"] if docente else "")

    try:
        from services.proyecto_service import registrar_proyecto
        proyecto = registrar_proyecto(
            docente_id=docente_id,
            titulo=datos.get("titulo", "Sin título"),
            linea=datos.get("linea", "Sistemas Inteligentes Aplicados"),
            objetivo=datos.get("objetivo", ""),
            actividades=datos.get("actividades", ""),
            responsable=responsable,
            producto=datos.get("producto", ""),
            periodo=periodo,
        )
        # Volver al estado autenticado
        estado["paso"] = "autenticado"
        estado.pop("campo_actual", None)
        estado.pop("datos_proyecto", None)
        estado.pop("semestre_registro", None)

        return (
            f"✅ **Proyecto registrado exitosamente** (ID: {proyecto['id']})\n\n"
            f"📌 **Título:** {proyecto['titulo']}\n"
            f"🔬 **Línea:** {proyecto['linea'] or '—'}\n"
            f"📅 **Período:** {proyecto['periodo'] or semestre}\n\n"
            "El proyecto queda en estado **pendiente de revisión** hasta que un "
            "docente lo apruebe. Puede consultarlo en `/admin/proyectos/pendientes`."
        )
    except Exception as exc:
        logger.error("Error al registrar proyecto desde chat: %s", exc)
        estado["paso"] = "autenticado"
        estado.pop("campo_actual", None)
        estado.pop("datos_proyecto", None)
        return f"❌ No se pudo guardar el proyecto: {exc}"


# ── Ruta de descarga ─────────────────────────────────────────────────────────

@router.get("/descargar/{tipo}/{filename}")
async def descargar(tipo: str, filename: str):
    """
    Sirve un PDF generado con el nombre oficial de descarga.
    El archivo en disco tiene nombre único interno; el navegador lo descarga
    con el nombre oficial según el Content-Disposition.
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


# ── Subida de documento para las secciones 2/3/4 del FO-IN-17 ────────────────

_EXTENSIONES_DOCUMENTO_PERMITIDAS = {"txt", "docx", "pdf"}


@router.post("/chat/documento")
async def subir_documento(
    session_id: str = Form(...),
    archivo: UploadFile = File(...),
):
    """
    Recibe el archivo (.txt/.docx/.pdf) que el docente sube para llenar las
    secciones 2/3/4 del FO-IN-17 sin digitarlas paso a paso (paso de sesión
    "esperando_documento_fo17"). Extrae el texto, lo estructura con Gemini y
    deja un resumen para confirmación (paso "confirmando_documento_fo17").

    Endpoint separado y multipart (no JSON) porque el contrato de `/chat` no
    admite archivos; el contrato de respuesta es el mismo (`{"reply": str}`)
    para que el frontend reutilice el mismo renderizado de mensajes del bot.
    """
    t_inicio = time.monotonic()
    estado = sesiones_activas.get(session_id)
    respuesta = ""
    exito = True
    nombre_archivo = archivo.filename or ""

    try:
        if not estado or estado.get("paso") != "esperando_documento_fo17":
            exito = False
            respuesta = (
                "⚠️ No estoy esperando ningún archivo en este momento. Pide de nuevo el "
                "Plan de Acción (FO-IN-17) y elige la opción de subir un documento."
            )
            return {"reply": respuesta}

        extension = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else ""
        if extension not in _EXTENSIONES_DOCUMENTO_PERMITIDAS:
            exito = False
            respuesta = (
                f"⚠️ Extensión '.{extension}' no soportada. Sube un archivo .txt, .docx o .pdf, "
                "o escribe **paso a paso** para digitar la información."
            )
            return {"reply": respuesta}

        contenido = await archivo.read()
        try:
            texto = extraer_texto(nombre_archivo, contenido)
        except ValueError as exc:
            exito = False
            respuesta = f"⚠️ {exc}"
            return {"reply": respuesta}

        periodo_actual = estado.get("fo17_semestre") or calcular_periodo()[0]
        datos = await asyncio.to_thread(estructurar_secciones_desde_texto, texto, periodo_actual)

        trabajos = datos.get("trabajos_grado", [])
        eventos = datos.get("eventos", [])
        fechas_otras = datos.get("fechas_otras_actividades", {})
        n_fechas = sum(1 for v in fechas_otras.values() if v)

        if not trabajos and not eventos and not n_fechas:
            respuesta = (
                f"No pude extraer información reconocible de **{nombre_archivo}** (¿trabajos "
                "de grado, eventos o fechas de otras actividades?).\n\n"
                "Puedes intentar con otro archivo, o escribe **paso a paso** para digitar la "
                "información directamente en el chat."
            )
            return {"reply": respuesta}

        estado["doc_parsed"] = datos
        estado["paso"] = "confirmando_documento_fo17"

        lineas = [
            f"📄 Encontré en **{nombre_archivo}**:",
            f"- **{len(trabajos)}** trabajo(s) de grado dirigido(s)",
            f"- **{len(eventos)}** evento(s)",
            f"- **{n_fechas}** fecha(s) de otras actividades",
        ]
        if trabajos:
            lineas.append("\n**Trabajos de grado:**")
            lineas.extend(
                f"- {t.get('titulo') or '(sin título)'} — {t.get('estudiante') or '(sin estudiante)'}"
                for t in trabajos
            )
        if eventos:
            lineas.append("\n**Eventos:**")
            lineas.extend(
                f"- {ev.get('nombre') or '(sin nombre)'} ({ev.get('fecha') or 'sin fecha'})"
                for ev in eventos
            )
        lineas.append("\n¿Confirmas que use esta información para completar el Plan de Acción? (**sí**/**no**)")
        respuesta = "\n".join(lineas)
        return {"reply": respuesta}

    except Exception as exc:
        logger.error("Error en /chat/documento session=%s: %s", session_id, exc)
        exito = False
        respuesta = "❌ Ocurrió un error inesperado al procesar el archivo. Intenta de nuevo."
        return {"reply": respuesta}

    finally:
        tiempo_ms = round((time.monotonic() - t_inicio) * 1000)
        docente_id = estado["docente"].get("id") if estado and estado.get("docente") else None
        try:
            from services.log_service import registrar_log
            registrar_log(
                session_id=session_id,
                mensaje_usuario=f"[documento subido: {nombre_archivo}]",
                respuesta_bot=respuesta,
                intencion_detectada="documento_fo_in_17",
                tiempo_respuesta_ms=tiempo_ms,
                exito=exito,
                docente_id=docente_id,
            )
        except Exception as exc_log:
            logger.debug("No se pudo registrar log de /chat/documento: %s", exc_log)


# ── Endpoint principal de chat ────────────────────────────────────────────────

@router.post("/chat")
async def chat(data: Message):
    t_inicio = time.monotonic()
    mensaje = data.message
    session_id = data.session_id if data.session_id else "default"

    estado = sesiones_activas.get(session_id)
    intencion = "chat_normal"
    fuentes_log: list[str] = []
    respuesta = ""
    exito = True

    try:
        semestre_actual, _, _ = calcular_periodo()
        autenticado = (
            estado is not None
            and estado.get("autenticado")
            and estado.get("paso") == "autenticado"
        )

        # ── 1. Flujo de autenticación en curso ─────────────────────────────
        if estado and estado.get("paso") == "esperando_usuario":
            intencion = "autenticacion"
            sesiones_activas[session_id]["usuario_ingresado"] = mensaje.strip()
            sesiones_activas[session_id]["paso"] = "esperando_password"
            respuesta = "🔐 Ahora ingresa tu contraseña:"
            return {"reply": respuesta}

        if estado and estado.get("paso") == "esperando_password":
            intencion = "autenticacion"
            usuario = estado["usuario_ingresado"]
            docente = verificar_credenciales(usuario, mensaje.strip())

            if docente:
                sesiones_activas[session_id]["autenticado"] = True
                sesiones_activas[session_id]["docente"] = docente
                sesiones_activas[session_id]["paso"] = "autenticado"

                flujo_post = estado.get("flujo_post_auth")
                if flujo_post == "registrar_proyecto":
                    respuesta = (
                        f"✅ Bienvenido/a, **{docente['nombre']}**. Acceso verificado.\n\n"
                        + _iniciar_registro_proyecto(session_id, docente, semestre_actual)
                    )
                    intencion = "registrar_proyecto"
                    return {"reply": respuesta}

                pdfs = estado.get("pdfs_solicitados", [])
                forzar_17 = estado.get("forzar_recoleccion_17", False)
                respuesta_docs, fuentes_log = await _entregar_pdfs_o_selector(
                    session_id, pdfs, docente, forzar_recoleccion_17=forzar_17,
                )
                intencion = f"solicitud_pdf_{pdfs}"
                respuesta = f"✅ Bienvenido/a, **{docente['nombre']}**. Acceso verificado.\n\n" + respuesta_docs
            else:
                del sesiones_activas[session_id]
                intencion = "autenticacion_fallida"
                exito = False
                respuesta = (
                    "❌ Usuario o contraseña incorrectos. "
                    "Si deseas intentarlo de nuevo, vuelve a solicitar el documento o el registro."
                )
            return {"reply": respuesta}

        # ── 2. Flujo de registro de proyecto en curso ───────────────────────
        if estado and estado.get("paso") == "registrando_proyecto":
            intencion = "registrar_proyecto"
            respuesta = await _procesar_registro_proyecto(session_id, mensaje)
            return {"reply": respuesta}

        # ── 2b. Confirmación del FO-IN-17 fuente antes de generar FO-IN-13 ──
        if estado and estado.get("paso") == "confirmando_fo_in_13":
            intencion = "solicitud_pdf_[13]"
            respuesta = await _procesar_confirmacion_fo_in_13(session_id, mensaje)
            return {"reply": respuesta}

        # ── 2c. Flujo de % de cumplimiento del FO-IN-13 en curso ────────────
        if estado and estado.get("paso") == "recolectando_cumplimiento":
            intencion = "solicitud_pdf_[13]"
            respuesta = await _procesar_cumplimiento(session_id, mensaje)
            return {"reply": respuesta}

        # ── 2d. Flujo de recolección conversacional del FO-IN-17 en curso ───
        if estado and estado.get("paso") == "recolectando_fo_in_17":
            intencion = "solicitud_pdf_[17]"
            respuesta = await _procesar_recoleccion_fo_in_17(session_id, mensaje)
            return {"reply": respuesta}

        # ── 2e. Selector de informe existente vs. nuevo en curso ────────────
        if estado and estado.get("paso") == "seleccionando_reporte":
            intencion = "seleccion_reporte"
            respuesta = await _procesar_seleccion_reporte(session_id, mensaje)
            return {"reply": respuesta}

        # ── 2f. Esperando el archivo adjunto para secciones 2/3/4 del FO-IN-17 ─
        if estado and estado.get("paso") == "esperando_documento_fo17":
            intencion = "documento_fo_in_17"
            respuesta = await _procesar_esperando_documento(session_id, mensaje)
            return {"reply": respuesta}

        # ── 2g. Confirmación del resumen extraído del documento subido ──────
        if estado and estado.get("paso") == "confirmando_documento_fo17":
            intencion = "documento_fo_in_17"
            respuesta = await _procesar_confirmacion_documento(session_id, mensaje)
            return {"reply": respuesta}

        # ── 3. Flujo de completado de PDF en curso ──────────────────────────
        if estado and estado.get("paso") == "completando_pdf":
            intencion = "completar_pdf"
            campos = estado["campos_pendientes"]
            idx_actual = estado["campo_actual"]

            campo = campos[idx_actual]
            estado["datos_recolectados"][campo] = mensaje.strip()

            if idx_actual + 1 < len(campos):
                estado["campo_actual"] += 1
                siguiente_campo = campos[estado["campo_actual"]]
                respuesta = f"Gracias. Ahora, dime el **{siguiente_campo.replace('_', ' ').title()}**:"
            else:
                pdf_numero = estado["pdf_numero"]
                datos = estado["datos_recolectados"]
                try:
                    output_path = pdf_completer.completar_pdf(pdf_numero, datos)
                    nombre_archivo = os.path.basename(output_path)
                    del sesiones_activas[session_id]
                    respuesta = (
                        f"✅ ¡PDF completado exitosamente!\n\n"
                        f"📄 Descarga tu documento aquí: [Descargar](/download/{nombre_archivo})"
                    )
                except Exception as e:
                    respuesta = f"❌ Ocurrió un error al generar el PDF: {str(e)}"
                    exito = False
            return {"reply": respuesta}

        # ── 3b. Detección de "actualizar plan de acción" ─────────────────────
        # Debe ir ANTES de detectar_pdf_solicitado: "plan de acción" ya es
        # alias del FO-IN-17 y entregaría el cacheado sin re-preguntar.
        if any(alias in mensaje.lower() for alias in ALIASES_ACTUALIZAR_17):
            intencion = "actualizar_fo_in_17"
            if autenticado:
                respuesta, fuentes_log = await iniciar_entrega_pdfs(
                    session_id, [17], estado["docente"], forzar_recoleccion_17=True,
                )
            else:
                sesiones_activas[session_id] = {
                    "paso": "esperando_usuario",
                    "pdfs_solicitados": [17],
                    "flujo_post_auth": None,
                    "forzar_recoleccion_17": True,
                    "usuario_ingresado": None,
                    "autenticado": False,
                    "docente": None,
                }
                respuesta = (
                    "🔒 Para actualizar el plan de acción necesito verificar tu identidad.\n\n"
                    "👤 Por favor ingresa tu **usuario**:"
                )
            return {"reply": respuesta}

        # ── 4. Detección de solicitud de PDFs ──────────────────────────────
        pdfs_solicitados = detectar_pdf_solicitado(mensaje, data.history)
        if pdfs_solicitados:
            intencion = f"solicitud_pdf_{pdfs_solicitados}"
            if autenticado:
                respuesta, fuentes_log = await _entregar_pdfs_o_selector(
                    session_id, pdfs_solicitados, estado["docente"]
                )
            else:
                sesiones_activas[session_id] = {
                    "paso": "esperando_usuario",
                    "pdfs_solicitados": pdfs_solicitados,
                    "flujo_post_auth": None,
                    "usuario_ingresado": None,
                    "autenticado": False,
                    "docente": None,
                }
                respuesta = (
                    "🔒 Para acceder a los documentos del semillero necesito verificar "
                    "tu identidad.\n\n👤 Por favor ingresa tu **usuario**:"
                )
            return {"reply": respuesta}

        # ── 5. Detección de intención de registrar proyecto ─────────────────
        if detectar_intencion_registrar(mensaje):
            intencion = "registrar_proyecto"
            if autenticado:
                respuesta = _iniciar_registro_proyecto(
                    session_id, estado["docente"], semestre_actual
                )
            else:
                sesiones_activas[session_id] = {
                    "paso": "esperando_usuario",
                    "pdfs_solicitados": [],
                    "flujo_post_auth": "registrar_proyecto",
                    "usuario_ingresado": None,
                    "autenticado": False,
                    "docente": None,
                }
                respuesta = (
                    "🔒 Para registrar un proyecto necesito verificar tu identidad.\n\n"
                    "👤 Por favor ingresa tu **usuario**:"
                )
            return {"reply": respuesta}

        # ── 6. Detección de intención de completar PDF ──────────────────────
        pdf_a_completar = detectar_intencion_completar(mensaje)
        if pdf_a_completar:
            intencion = f"completar_pdf_{pdf_a_completar}"
            if autenticado:
                sesiones_activas[session_id]["paso"] = "completando_pdf"
                sesiones_activas[session_id]["pdf_numero"] = pdf_a_completar
                sesiones_activas[session_id]["datos_recolectados"] = {}
                sesiones_activas[session_id]["campo_actual"] = 0
                sesiones_activas[session_id]["campos_pendientes"] = (
                    pdf_completer.PDF_CONFIG[pdf_a_completar]["campos"]
                )
                primer_campo = sesiones_activas[session_id]["campos_pendientes"][0]
                respuesta = (
                    f"📝 Vamos a completar el **{pdf_completer.PDF_CONFIG[pdf_a_completar]['descripcion']}**.\n\n"
                    f"Por favor, dime el **{primer_campo.replace('_', ' ').title()}**:"
                )
            else:
                sesiones_activas[session_id] = {
                    "paso": "esperando_usuario",
                    "pdfs_solicitados": [pdf_a_completar],
                    "flujo_post_auth": None,
                    "usuario_ingresado": None,
                    "autenticado": False,
                    "docente": None,
                }
                respuesta = (
                    "🔒 Para acceder a los documentos necesito verificar tu identidad.\n\n"
                    "👤 Por favor ingresa tu **usuario**:"
                )
            return {"reply": respuesta}

        # ── 7. Chat normal ──────────────────────────────────────────────────
        intencion = "chat_normal"
        respuesta = generar_respuesta(mensaje, data.history, session_id)
        return {"reply": respuesta}

    except Exception as exc:
        logger.error("Error en /chat session=%s: %s", session_id, exc)
        exito = False
        respuesta = "❌ Ocurrió un error inesperado. Por favor, intenta de nuevo."
        return {"reply": respuesta}

    finally:
        tiempo_ms = round((time.monotonic() - t_inicio) * 1000)
        docente_id = None
        if estado and estado.get("docente"):
            docente_id = estado["docente"].get("id")
        try:
            from services.log_service import registrar_log
            import json
            registrar_log(
                session_id=session_id,
                mensaje_usuario=mensaje,
                respuesta_bot=respuesta,
                intencion_detectada=str(intencion),
                fuentes_usadas=json.dumps(fuentes_log, ensure_ascii=False) if fuentes_log else "",
                tiempo_respuesta_ms=tiempo_ms,
                exito=exito,
                docente_id=docente_id,
            )
        except Exception as exc_log:
            logger.debug("No se pudo registrar log: %s", exc_log)
