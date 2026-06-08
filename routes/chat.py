"""
Router principal de chat de GIAbot.

Maneja en orden de prioridad:
  1. Flujos de autenticación en curso (usuario/contraseña).
  2. Flujo de registro conversacional de proyectos en curso.
  3. Confirmación del FO-IN-17 fuente antes de generar el FO-IN-13.
  4. Flujo de % de cumplimiento del FO-IN-13 en curso.
  5. Flujo de completado de PDF en curso.
  6. Detección de solicitud de PDFs (FO-IN-13 / FO-IN-17).
  7. Detección de intención de registrar proyecto.
  8. Detección de intención de completar PDF.
  9. Chat normal delegado al LLM.

Todas las interacciones quedan registradas en conversation_logs.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime
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
from services.fo_in_13_service import generar_fo_in_13, obtener_fuente_fo_in_13
from services.fo_in_17_service import generar_fo_in_17
from services.pdf_fo_in_13 import proyectos_validos


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


def detectar_docente_solicitado(mensaje: str) -> dict | None:
    """
    Detecta si el mensaje menciona a un docente específico del GIA.
    Retorna {"nombre": ..., "cvlac_url": ...} o None.
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
        logger.info("Docente solicitado detectado: %s (%d coincidencias)", mejor["nombre"], mejor_hits)
    return mejor


# ── Construcción de respuesta con extracción de PDFs ────────────────────────

def _nota_objetivo(docente_objetivo: dict | None) -> str:
    return (
        f"\n👤 Proyectos atribuidos a: **{docente_objetivo['nombre']}**"
        if docente_objetivo else ""
    )


async def _bloque_fo_in_17(
    docente: dict | None,
    docente_objetivo: dict | None,
    semestre_actual: str,
) -> tuple[str, list[str]]:
    """Genera el FO-IN-17 y devuelve (bloque_markdown, fuentes)."""
    fuentes: list[str] = []
    try:
        resultado_17 = await asyncio.to_thread(
            generar_fo_in_17, docente, semestre_actual, docente_objetivo
        )
        nombre_17 = resultado_17["pdf_nombre"]
        advertencia = resultado_17.get("advertencia", "")
        fuentes.extend(resultado_17.get("datos", {}).get("fuentes_consultadas", []))
        bloque = (
            "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
            f"Semestre: {semestre_actual}{_nota_objetivo(docente_objetivo)}\n"
            f"👉 [Descargar informe (PDF)](/descargar/17/{nombre_17})"
        )
        if advertencia:
            bloque += f"\n⚠️ {advertencia}"
        _guardar_reporte_asinc(docente, "17", semestre_actual, nombre_17, fuentes, docente_objetivo)
        return bloque, fuentes
    except Exception as exc:
        return (
            "📄 **FO-IN-17 – Plan de Acción de Grupos de Investigación**\n"
            f"⚠️ No se pudo generar el PDF: {exc}\n"
            "👉 [Descargar plantilla base](/static/docs/FO-IN-17%20PLAN%20DE%20ACCION%20GRUPOS%20INV%20V1.pdf)",
            fuentes,
        )


async def _bloque_fo_in_13(
    docente: dict | None,
    docente_objetivo: dict | None,
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
                docente, semestre_actual, docente_objetivo,
                datos_fuente=datos_fuente, sem_referencia=sem_referencia,
                responsable_base=responsable_base,
                cumplimientos=cumplimientos,
            )
        )
        nombre_13 = resultado_13["pdf_nombre"]
        sem_ref = resultado_13["semestre_referencia"]
        responsable_base = resultado_13.get("responsable_base", "")
        _guardar_reporte_asinc(docente, "13", semestre_actual, nombre_13, [],
                               docente_objetivo or ({"nombre": responsable_base} if responsable_base else None))
        nota_fuente = f" — basado en datos de **{responsable_base}**" if responsable_base else ""
        return (
            "📄 **FO-IN-13 – Informe de Gestión de Grupos de Investigación**\n"
            f"Generado a partir del FO-IN-17 del semestre {sem_ref}{nota_fuente}."
            f"{_nota_objetivo(docente_objetivo)}\n"
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
    docente_objetivo: dict | None,
) -> tuple[str, list[str]]:
    """
    Orquesta la entrega de los PDFs solicitados.

    - FO-IN-17 se genera y entrega de inmediato.
    - FO-IN-13: primero muestra los metadatos del FO-IN-17 que se usará como
      fuente y pide confirmación (estado `confirmando_fo_in_13`). Cuando el
      docente confirma, `_procesar_confirmacion_fo_in_13` inicia el flujo de
      % de cumplimiento o genera directo si no hay proyectos.

    Retorna (texto_respuesta, fuentes).
    """
    semestre_actual, _, _ = calcular_periodo()
    partes: list[str] = []
    fuentes: list[str] = []
    inicio_preguntas = False

    if 17 in pdfs:
        bloque_17, fuentes_17 = await _bloque_fo_in_17(docente, docente_objetivo, semestre_actual)
        partes.append(bloque_17)
        fuentes.extend(fuentes_17)

    if 13 in pdfs:
        try:
            fuente = await asyncio.to_thread(
                obtener_fuente_fo_in_13, docente, semestre_actual, docente_objetivo
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
                "confirmacion_docente_objetivo": docente_objetivo,
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

    if inicio_preguntas:
        # Aún no se entrega el FO-IN-13; se inician las preguntas de %.
        intro = "Aquí tienes el FO-IN-17 solicitado:" if 17 in pdfs else ""
    else:
        intro = (
            "Aquí tienes los documentos solicitados:"
            if len(pdfs) > 1
            else "Aquí tienes el documento solicitado:"
        )
    return (intro + "\n\n" + "\n\n".join(partes)).strip(), fuentes


async def _continuar_generacion_fo_in_13(
    session_id: str,
    docente: dict | None,
    docente_objetivo: dict | None,
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
            "cumpl_docente_objetivo": docente_objetivo,
        })
        return (
            f"Para generar el **FO-IN-13** necesito el porcentaje de cumplimiento "
            f"de cada uno de los {len(proyectos)} proyectos del Plan de Acción "
            f"(FO-IN-17) del semestre {sem_ref}.\n\n"
            + _pregunta_cumplimiento(proyectos[0])
        )

    return await _bloque_fo_in_13(
        docente, docente_objetivo, semestre_actual,
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
            "confirmacion_responsable_base", "confirmacion_docente_objetivo",
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
        docente_objetivo = estado.pop("confirmacion_docente_objetivo", None)
        docente = estado.get("docente")
        estado["paso"] = "autenticado"

        return await _continuar_generacion_fo_in_13(
            session_id, docente, docente_objetivo,
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
        estado.get("cumpl_docente_objetivo"),
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
        "cumpl_docente_objetivo",
    ):
        estado.pop(clave, None)

    return "✅ ¡Listo! Generé tu informe con los porcentajes indicados.\n\n" + bloque


def _guardar_reporte_asinc(docente, tipo, semestre, pdf_nombre, fuentes, docente_objetivo=None):
    """Guarda el registro del reporte generado sin bloquear la respuesta."""
    try:
        import json
        from services.rag_service import guardar_reporte
        docente_id = docente["id"] if docente else None
        responsable_nombre = (
            docente_objetivo["nombre"] if docente_objetivo
            else (docente["nombre"] if docente else None)
        )
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
                docente_obj = estado.get("docente_solicitado")
                respuesta_docs, fuentes_log = await iniciar_entrega_pdfs(
                    session_id, pdfs, docente, docente_obj
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

        # ── 4. Detección de solicitud de PDFs ──────────────────────────────
        pdfs_solicitados = detectar_pdf_solicitado(mensaje, data.history)
        if pdfs_solicitados:
            intencion = f"solicitud_pdf_{pdfs_solicitados}"
            docente_solicitado = await asyncio.to_thread(detectar_docente_solicitado, mensaje)
            if autenticado:
                respuesta, fuentes_log = await iniciar_entrega_pdfs(
                    session_id, pdfs_solicitados, estado["docente"], docente_solicitado
                )
            else:
                sesiones_activas[session_id] = {
                    "paso": "esperando_usuario",
                    "pdfs_solicitados": pdfs_solicitados,
                    "docente_solicitado": docente_solicitado,
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
