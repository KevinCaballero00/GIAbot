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


def generar_fo_in_13(
    docente: dict,
    semestre_actual: str | None = None,
    docente_objetivo: dict | None = None,
) -> dict:
    """
    Genera el FO-IN-13 del docente usando el FO-IN-17 del semestre anterior.

    Flujo:
      1. Calcula el semestre anterior a semestre_actual.
      2. Busca en BD el FO-IN-17 de ese semestre.
      3. Si no existe, lo genera y persiste primero.
      4. Genera el PDF del FO-IN-13 desde el JSON del FO-IN-17 (sin scraping).

    Retorna dict con:
      - pdf_nombre: nombre del archivo para construir enlace de descarga
      - pdf_path: ruta absoluta del PDF generado
      - semestre_referencia: semestre del FO-IN-17 usado como fuente
      - datos_fuente: dict con proyectos del FO-IN-17 de referencia
    """
    if semestre_actual is None:
        semestre_actual, _, _ = calcular_periodo()

    sem_anterior = semestre_anterior(semestre_actual)

    logger.info(
        "FO-IN-13: generando para docente '%s' — semestre actual %s, referencia %s",
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

    # Preparar el dict de entrada para el generador de FO-IN-13
    # Se ajusta el periodo al semestre de referencia para que el encabezado sea correcto
    datos_fo_in_13 = dict(datos_fuente)
    datos_fo_in_13["periodo"] = sem_anterior

    pdf_path = generar_pdf_fo_in_13_plantilla(datos_fo_in_13)
    pdf_nombre = Path(pdf_path).name

    return {
        "pdf_path": pdf_path,
        "pdf_nombre": pdf_nombre,
        "semestre_referencia": sem_anterior,
        "datos_fuente": datos_fuente,
    }
