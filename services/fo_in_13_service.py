"""
Servicio de generación del FO-IN-13.

FO-IN-13 es un documento derivado: se construye a partir del JSON canónico
del FO-IN-17 más reciente válido guardado en la base de datos.

La fuente no depende del docente autenticado ni del semestre anterior por
defecto; siempre usa el último FO-IN-17 con estado='ok' ordenado por fecha
de generación/refresco descendente. Solo si no existe ninguno se genera uno
nuevo sobre la marcha.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from services.extractor_proyectos import calcular_periodo
from services.fo_in_17_service import (
    generar_fo_in_17,
    obtener_ultimo_fo_in_17_valido,
    semestre_anterior,
)
from services.pdf_fo_in_13 import generar_pdf_fo_in_13_plantilla

logger = logging.getLogger(__name__)


def obtener_fuente_fo_in_13(
    docente: dict,
    semestre_actual: str | None = None,
    docente_objetivo: dict | None = None,
) -> dict:
    """
    Obtiene los datos fuente del FO-IN-13: el FO-IN-17 más reciente válido.

    No genera el PDF; solo resuelve de dónde salen los proyectos. Se usa antes
    de preguntar al docente el % de cumplimiento, y el dict resultante se
    reutiliza en `generar_fo_in_13` para no scrapear dos veces.

    Lógica de resolución:
      1. Buscar el FO-IN-17 global más reciente con estado='ok'.
      2. Si no existe ninguno, generar uno nuevo para el docente actual
         (o el objetivo si se indicó) del semestre anterior.

    Retorna dict con:
      - datos_fuente: dict con proyectos del FO-IN-17 de referencia
      - sem_referencia: semestre al que pertenece ese FO-IN-17
      - responsable_base: nombre del responsable del FO-IN-17 usado
    """
    if semestre_actual is None:
        semestre_actual, _, _ = calcular_periodo()

    sem_anterior = semestre_anterior(semestre_actual)

    # 1. Usar el último FO-IN-17 válido disponible globalmente
    registro = obtener_ultimo_fo_in_17_valido()
    if registro and registro.get("datos_json"):
        datos_fuente = json.loads(registro["datos_json"])
        sem_ref = registro.get("semestre", sem_anterior)
        responsable_base = registro.get("responsable_nombre") or datos_fuente.get("responsable", "")
        fo_in_17_fecha = registro.get("fecha_refresco") or registro.get("fecha_creacion", "")
        fo_in_17_generado_por = registro.get("generado_por_nombre", "")
        logger.info(
            "FO-IN-13: usando último FO-IN-17 válido — responsable '%s', semestre %s",
            responsable_base, sem_ref,
        )
        return {
            "datos_fuente": datos_fuente,
            "sem_referencia": sem_ref,
            "responsable_base": responsable_base,
            "fo_in_17_fecha": fo_in_17_fecha,
            "fo_in_17_generado_por": fo_in_17_generado_por,
        }

    # 2. No existe ningún FO-IN-17 válido — generar uno nuevo
    logger.info(
        "FO-IN-13: no hay FO-IN-17 válido en BD, generando para '%s', semestre %s...",
        (docente_objetivo or docente).get("nombre"), sem_anterior,
    )
    resultado_17 = generar_fo_in_17(docente, sem_anterior, docente_objetivo=docente_objetivo)
    datos_fuente = resultado_17["datos"]
    responsable_base = datos_fuente.get("responsable", (docente_objetivo or docente).get("nombre", ""))
    registro_nuevo = resultado_17.get("registro") or {}
    return {
        "datos_fuente": datos_fuente,
        "sem_referencia": sem_anterior,
        "responsable_base": responsable_base,
        "fo_in_17_fecha": registro_nuevo.get("fecha_creacion", ""),
        "fo_in_17_generado_por": docente.get("nombre", ""),
    }


def generar_fo_in_13(
    docente: dict,
    semestre_actual: str | None = None,
    docente_objetivo: dict | None = None,
    *,
    datos_fuente: dict | None = None,
    sem_referencia: str | None = None,
    responsable_base: str | None = None,
    cumplimientos: dict | None = None,
) -> dict:
    """
    Genera el FO-IN-13 usando el FO-IN-17 más reciente como fuente.

    Si `datos_fuente`/`sem_referencia` se proporcionan (ya obtenidos por
    `obtener_fuente_fo_in_13` durante la fase de preguntas), se reutilizan
    sin volver a leer la BD.

    `cumplimientos` es un dict {idx: "90%"} con los porcentajes que el
    docente indicó para cada proyecto.

    Retorna dict con:
      - pdf_nombre: nombre del archivo para construir el enlace de descarga
      - pdf_path: ruta absoluta del PDF generado
      - semestre_referencia: semestre del FO-IN-17 usado como fuente
      - responsable_base: responsable del FO-IN-17 de referencia
      - datos_fuente: dict con proyectos del FO-IN-17 de referencia
    """
    if datos_fuente is None or sem_referencia is None:
        fuente = obtener_fuente_fo_in_13(docente, semestre_actual, docente_objetivo)
        datos_fuente = fuente["datos_fuente"]
        sem_referencia = fuente["sem_referencia"]
        responsable_base = responsable_base or fuente.get("responsable_base", "")

    datos_fo_in_13 = dict(datos_fuente)
    datos_fo_in_13["periodo"] = sem_referencia
    datos_fo_in_13["cumplimientos"] = cumplimientos or {}

    pdf_path = generar_pdf_fo_in_13_plantilla(datos_fo_in_13)
    pdf_nombre = Path(pdf_path).name

    return {
        "pdf_path": pdf_path,
        "pdf_nombre": pdf_nombre,
        "semestre_referencia": sem_referencia,
        "responsable_base": responsable_base or "",
        "datos_fuente": datos_fuente,
    }
