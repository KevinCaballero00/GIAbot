"""
Estructurador de proyectos con Gemini.

El scraping del CvLAC y de la página del GIA devuelve texto crudo y desordenado.
Este módulo usa el LLM (Gemini) para convertir ese texto en una lista limpia de
líneas de investigación con los campos exactos que necesita el FO-IN-17:

    linea, proyecto, objetivo, actividades (lista), responsable, producto

Regla de oro: NO inventar datos. Si un campo no aparece en el texto, queda vacío.
El número de líneas se limita a `max_lineas` (5-6) porque el formato oficial del
GIA contempla a lo sumo ese número de líneas de investigación.
"""
from __future__ import annotations

import json
import logging

from google.genai import types

from services.ai_service import client

logger = logging.getLogger(__name__)

MODELO = "models/gemini-2.5-flash"

_PROMPT = """Eres un asistente que estructura información de investigación académica
para el formato oficial FO-IN-17 (Plan de Acción de Grupos de Investigación) de la
Universidad Francisco de Paula Santander.

A continuación recibirás TEXTO CRUDO extraído de perfiles CvLAC de varios docentes
del grupo GIA. El texto trae los proyectos agrupados en bloques, cada uno precedido
por una línea "Docente: <nombre>" que indica a quién pertenece ese bloque. Tu tarea
es identificar los proyectos de investigación MÁS RECIENTES del grupo (no de un solo
docente) y devolverlos como JSON limpio.

Periodo académico actual: "{periodo_actual}"

Devuelve EXCLUSIVAMENTE un arreglo JSON (sin texto adicional) con MÁXIMO {max_lineas}
objetos. Cada objeto debe tener exactamente estas claves:
  - "linea": nombre de la línea de investigación (ej. "Sistemas Inteligentes Aplicados").
  - "proyecto": título del proyecto a ejecutar (conciso, sin el resumen completo).
  - "objetivo": objetivo principal del proyecto (1-2 frases).
  - "actividades": lista de strings con las actividades principales (máx. 5 ítems cortos).
    Si las actividades no aparecen explícitas en el texto, DEDÚCELAS lógicamente del
    objetivo y descripción del proyecto (p. ej. "Revisión de literatura", "Diseño
    metodológico", "Recolección y análisis de datos", "Redacción de artículo"). No
    dejes este campo como lista vacía si hay información del proyecto disponible.
  - "responsable": nombre EXACTO del docente indicado en el "Docente:" del bloque de
    origen de ese proyecto. No inventes ni asumas un responsable distinto al del bloque.
  - "producto": producto esperado (ponencia, artículo, software, prototipo, etc.).
    Si no aparece explícitamente, infiere el producto académico más probable según
    el tipo de proyecto. No dejes vacío si hay descripción del proyecto.
  - "periodo": el rango o año del proyecto tal como aparece en el texto (ej. "2025 -",
    "2024 - 2026"). Cadena vacía "" si no hay fecha visible.

Selección de proyectos:
  - Prioriza los proyectos más recientes: cuyo rango de fechas incluya el año de
    "{periodo_actual}" o esté abierto (sin fecha de fin).
  - Si no hay suficientes proyectos vigentes para llenar los {max_lineas} cupos,
    completa con los del año inmediatamente anterior.
  - Ordena el resultado del proyecto más reciente al más antiguo.
  - Diversidad: si hay más candidatos que cupos, no incluyas más de 2 proyectos del
    mismo docente para que el plan grupal represente a varios docentes.

Reglas estrictas:
  - NO inventes títulos de proyecto, objetivos ni nombre de responsable; si no
    aparecen en el texto déjalos como cadena vacía "".
  - Para "actividades" y "producto" sí puedes deducir a partir del objetivo/descripción.
  - Agrupa información que pertenezca al mismo proyecto; no dupliques líneas.
  - Si no hay proyectos identificables, devuelve un arreglo vacío [].

TEXTO CRUDO:
---
{texto}
---
"""


def estructurar_proyectos(
    texto_crudo: str,
    periodo_actual: str,
    max_lineas: int = 6,
) -> list[dict]:
    """
    Estructura el texto crudo (multi-docente) en una lista grupal de líneas de
    investigación limpias.

    Retorna una lista de dicts con claves: linea, proyecto, objetivo,
    actividades (list[str]), responsable, producto, periodo. Devuelve [] si Gemini
    falla o no encuentra proyectos (el llamador decide el fallback).
    """
    texto = (texto_crudo or "").strip()
    if not texto:
        return []

    # Acotar el texto para no exceder límites del modelo
    texto = texto[:40000]

    prompt = _PROMPT.format(
        periodo_actual=periodo_actual or "",
        max_lineas=max_lineas,
        texto=texto,
    )

    try:
        response = client.models.generate_content(
            model=MODELO,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )
        data = json.loads(response.text)
    except Exception as exc:
        logger.warning("Estructurador: fallo al estructurar con Gemini – %s", exc)
        return []

    if not isinstance(data, list):
        logger.warning("Estructurador: respuesta no es lista (%s)", type(data).__name__)
        return []

    proyectos: list[dict] = []
    for item in data[:max_lineas]:
        if not isinstance(item, dict):
            continue
        actividades = item.get("actividades") or []
        if isinstance(actividades, str):
            actividades = [actividades]
        proyectos.append({
            "linea": str(item.get("linea") or "").strip(),
            "proyecto": str(item.get("proyecto") or "").strip(),
            "objetivo": str(item.get("objetivo") or "").strip(),
            "actividades": [str(a).strip() for a in actividades if str(a).strip()],
            "responsable": str(item.get("responsable") or "").strip(),
            "producto": str(item.get("producto") or "").strip(),
            "periodo": str(item.get("periodo") or "").strip(),
        })

    logger.info("Estructurador: %d líneas estructuradas (grupal, periodo '%s')", len(proyectos), periodo_actual)
    return proyectos
