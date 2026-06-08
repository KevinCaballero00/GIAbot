"""
Servicio de IA: genera respuestas usando Gemini con contexto del GIA.

Mejoras respecto a la versión original:
  - Inicialización tolerante a fallos (no tumba el startup si la API key falta).
  - Scraping cacheado a nivel de módulo con fallback seguro.
  - RAG: recupera chunks relevantes por consulta y los inyecta al contexto.
  - Función refrescar_contexto() para regenerar CONTEXTO_WEB sin reiniciar.
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

from models.knowledge import GIA_INFO

load_dotenv()

logger = logging.getLogger(__name__)

# ── Inicialización tolerante de Gemini ───────────────────────────────────────

try:
    from google import genai
    from google.genai import types as genai_types
    client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))
    logger.info("Gemini: cliente inicializado correctamente.")
except Exception as _exc_gemini:
    logger.error("Gemini: no se pudo inicializar el cliente: %s", _exc_gemini)
    client = None
    genai_types = None  # type: ignore[assignment]

# ── Contexto web cacheado (se carga en background, sin bloquear el arranque) ─

CONTEXTO_WEB: str = ""

try:
    from services.scraper import obtener_contexto_web
    CONTEXTO_WEB = obtener_contexto_web()
except Exception as _exc_scraper:
    logger.warning("Scraper: contexto web no disponible en arranque: %s", _exc_scraper)

# ── Prompt base del sistema ───────────────────────────────────────────────────

_INSTRUCCIONES_RAG = """
=====================
INSTRUCCIONES DE USO DEL CONTEXTO
=====================
- Usa PRIMERO el CONTEXTO RECUPERADO (RAG) si aparece en este turno; contiene
  información fresca de la BD o del sitio web del GIA relevante a la pregunta.
- Si el contexto recuperado contradice el contexto base, prioriza el recuperado.
- No inventes datos que no estén en ninguno de los dos contextos.
- Si no tienes información suficiente, dilo claramente y sugiere contactar al GIA.
"""

_PROMPT_BASE = (
    GIA_INFO
    + _INSTRUCCIONES_RAG
    + """
=====================
INFORMACIÓN ACTUALIZADA DE LA PÁGINA WEB DEL GIA
=====================
La siguiente información fue extraída directamente del sitio web oficial del
grupo GIA, incluyendo el contenido de los perfiles académicos de cada docente.

- Responde con datos concretos del contexto, no con links (salvo que los pidan).
- No inventes títulos, publicaciones ni datos académicos.

"""
)


def _construir_system_prompt() -> str:
    return _PROMPT_BASE + CONTEXTO_WEB


# ── Función pública ───────────────────────────────────────────────────────────

def generar_respuesta(mensaje: str, historial: list, session_id: str = "") -> str:
    """
    Genera una respuesta para `mensaje` usando Gemini + contexto RAG.

    Si el cliente Gemini no está disponible, devuelve un mensaje de error
    amigable en lugar de lanzar excepción.
    """
    if client is None or genai_types is None:
        return (
            "⚠️ El servicio de inteligencia artificial no está disponible en este "
            "momento. Por favor, intenta más tarde o contacta al administrador."
        )

    # Recuperar contexto RAG relevante para este mensaje
    contexto_rag = ""
    try:
        from services.rag_service import buscar_contexto_relevante
        contexto_rag = buscar_contexto_relevante(mensaje, top_k=4)
    except Exception as exc:
        logger.debug("RAG: no se pudo recuperar contexto: %s", exc)

    system_prompt = _construir_system_prompt()
    if contexto_rag:
        system_prompt = system_prompt + "\n" + contexto_rag

    historial_formateado = [
        genai_types.Content(
            role="model" if h["role"] == "assistant" else "user",
            parts=[genai_types.Part(text=h["content"])],
        )
        for h in historial
    ]

    try:
        chat = client.chats.create(
            model="models/gemini-2.5-flash",
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
            ),
            history=historial_formateado,
        )
        response = chat.send_message(mensaje)
        return response.text
    except Exception as exc:
        logger.error("Gemini: error generando respuesta: %s", exc)
        return (
            "⚠️ No pude procesar tu consulta en este momento. "
            "Por favor, intenta de nuevo."
        )


def refrescar_contexto() -> str:
    """
    Fuerza recarga del contexto web y repuebla los knowledge_chunks.
    Retorna mensaje de estado.
    """
    global CONTEXTO_WEB  # noqa: PLW0603
    try:
        from services.scraper import refrescar_contexto_web
        CONTEXTO_WEB = refrescar_contexto_web()
        try:
            from services.rag_service import poblar_chunks
            n = poblar_chunks(CONTEXTO_WEB)
            return f"Contexto actualizado: {n} chunks RAG regenerados."
        except Exception as exc_rag:
            logger.warning("RAG: error al repoblar chunks: %s", exc_rag)
            return "Contexto web actualizado (chunks RAG no se pudieron regenerar)."
    except Exception as exc:
        logger.error("Error al refrescar contexto: %s", exc)
        return f"Error al actualizar contexto: {exc}"
