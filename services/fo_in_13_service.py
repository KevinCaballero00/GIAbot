"""
Servicio de generación del FO-IN-13.

FO-IN-13 es un documento derivado: se construye a partir del JSON
canónico del FO-IN-17 del semestre inmediatamente anterior.
No realiza scraping directo — la fuente de verdad es la BD.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from services.extractor_proyectos import calcular_periodo
from services.fo_in_17_service import generar_fo_in_17, obtener_registro, semestre_anterior
from services.pdf_fo_in_13 import generar_pdf_fo_in_13_plantilla

logger = logging.getLogger(__name__)


def obtener_fuente_fo_in_13(
    docente: dict,
    semestre_actual: str | None = None,
    docente_objetivo: dict | None = None,
) -> dict:
    """
    Obtiene los datos fuente del FO-IN-13: el FO-IN-17 del semestre anterior.

    No genera el PDF; solo resuelve de dónde salen los proyectos. Se usa antes
    de preguntar al docente el % de cumplimiento de cada proyecto, y el dict
    resultante se reutiliza luego en `generar_fo_in_13(...)` para no scrapear dos
    veces.

    Retorna dict con:
      - datos_fuente: dict con proyectos del FO-IN-17 de referencia
      - sem_referencia: semestre del FO-IN-17 usado como fuente
    """
    if semestre_actual is None:
        semestre_actual, _, _ = calcular_periodo()

    sem_anterior = semestre_anterior(semestre_actual)

    logger.info(
        "FO-IN-13: obteniendo fuente para docente '%s' — semestre actual %s, referencia %s",
        docente.get("nombre"), semestre_actual, sem_anterior,
    )

    # Obtener o crear el FO-IN-17 del semestre anterior.
    # Si se pidió un docente objetivo, se regenera para reflejar a esa persona
    # (la caché está keyed solo por docente autenticado).
    registro_previo = obtener_registro(docente["id"], sem_anterior)
    if (
        docente_objetivo is None
        and registro_previo and registro_previo.get("datos_json")
        and registro_previo.get("estado") == "ok"
    ):
        datos_fuente = json.loads(registro_previo["datos_json"])
        logger.info(
            "FO-IN-13: usando FO-IN-17 existente del semestre %s", sem_anterior,
        )
    else:
        logger.info(
            "FO-IN-13: FO-IN-17 del semestre %s no disponible o con objetivo, generando...",
            sem_anterior,
        )
        resultado_17 = generar_fo_in_17(docente, sem_anterior, docente_objetivo=docente_objetivo)
        datos_fuente = resultado_17["datos"]

    return {"datos_fuente": datos_fuente, "sem_referencia": sem_anterior}


def generar_fo_in_13(
    docente: dict,
    semestre_actual: str | None = None,
    docente_objetivo: dict | None = None,
    *,
    datos_fuente: dict | None = None,
    sem_referencia: str | None = None,
    cumplimientos: dict | None = None,
) -> dict:
    """
    Genera el FO-IN-13 del docente usando el FO-IN-17 del semestre anterior.

    Si `datos_fuente`/`sem_referencia` se proporcionan (p. ej. ya obtenidos por
    `obtener_fuente_fo_in_13` durante la fase de preguntas), se reutilizan y no
    se vuelve a leer/scrapear el FO-IN-17.

    `cumplimientos` es un dict {titulo_proyecto: "90%"} con los porcentajes que
    el docente indicó para cada proyecto de la sección 1.

    Retorna dict con:
      - pdf_nombre: nombre del archivo para construir enlace de descarga
      - pdf_path: ruta absoluta del PDF generado
      - semestre_referencia: semestre del FO-IN-17 usado como fuente
      - datos_fuente: dict con proyectos del FO-IN-17 de referencia
    """
    if datos_fuente is None or sem_referencia is None:
        fuente = obtener_fuente_fo_in_13(docente, semestre_actual, docente_objetivo)
        datos_fuente = fuente["datos_fuente"]
        sem_referencia = fuente["sem_referencia"]

    # Preparar el dict de entrada para el generador de FO-IN-13
    # Se ajusta el periodo al semestre de referencia para que el encabezado sea correcto
    datos_fo_in_13 = dict(datos_fuente)
    datos_fo_in_13["periodo"] = sem_referencia
    datos_fo_in_13["cumplimientos"] = cumplimientos or {}

    pdf_path = generar_pdf_fo_in_13_plantilla(datos_fo_in_13)
    pdf_nombre = Path(pdf_path).name

    return {
        "pdf_path": pdf_path,
        "pdf_nombre": pdf_nombre,
        "semestre_referencia": sem_referencia,
        "datos_fuente": datos_fuente,
    }
