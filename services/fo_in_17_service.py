"""
Servicio de gestión del FO-IN-17.

FO-IN-17 es el documento fuente del semestre actual:
  - Se extrae de CvLAC y Google Scholar (via extractor_proyectos)
  - Se normaliza a JSON
  - Se persiste en la base de datos
  - Se refresca automáticamente si han pasado más de 15 días
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from models.database import get_connection, get_cursor
from services.extractor_proyectos import calcular_periodo, extraer_proyectos
from services.pdf_fo_in_17 import generar_pdf_fo_in_17_plantilla

logger = logging.getLogger(__name__)

INTERVALO_REFRESCO_DIAS = 15


# ── Utilidades de semestre ────────────────────────────────────────────────────

def semestre_anterior(semestre: str) -> str:
    """
    Calcula el semestre inmediatamente anterior.

    Regla:
      2026-1 → 2025-2
      2025-2 → 2025-1
    """
    anio_str, num_str = semestre.split("-")
    anio = int(anio_str)
    num = int(num_str)
    if num == 1:
        return f"{anio - 1}-2"
    return f"{anio}-1"


def _necesita_refresco(fecha_refresco: str | None) -> bool:
    """True si han pasado más de INTERVALO_REFRESCO_DIAS desde el último refresco."""
    if not fecha_refresco:
        return True
    try:
        ultimo = datetime.fromisoformat(fecha_refresco)
        return datetime.utcnow() - ultimo > timedelta(days=INTERVALO_REFRESCO_DIAS)
    except ValueError:
        return True


# ── Acceso a la BD ────────────────────────────────────────────────────────────

def obtener_registro(docente_id: int, semestre: str) -> dict | None:
    """Obtiene el registro FO-IN-17 de la BD; retorna None si no existe."""
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT * FROM fo_in_17 WHERE docente_id = %s AND semestre = %s",
            (docente_id, semestre),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def _guardar_registro(
    docente_id: int,
    semestre: str,
    datos_json: str,
    pdf_path: str,
    fuentes_usadas: str,
    estado: str = "ok",
    error_log: str | None = None,
) -> None:
    """Inserta o actualiza el registro en la tabla fo_in_17."""
    ahora = datetime.utcnow().isoformat()
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT id FROM fo_in_17 WHERE docente_id = %s AND semestre = %s",
            (docente_id, semestre),
        )
        existe = cur.fetchone()
        if existe:
            cur.execute(
                """
                UPDATE fo_in_17
                SET datos_json = %s, pdf_path = %s, fuentes_usadas = %s,
                    fecha_refresco = %s, estado = %s, error_log = %s
                WHERE docente_id = %s AND semestre = %s
                """,
                (datos_json, pdf_path, fuentes_usadas, ahora, estado, error_log,
                 docente_id, semestre),
            )
        else:
            cur.execute(
                """
                INSERT INTO fo_in_17
                  (docente_id, semestre, datos_json, pdf_path, fuentes_usadas,
                   fecha_creacion, fecha_refresco, estado, error_log)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (docente_id, semestre, datos_json, pdf_path, fuentes_usadas,
                 ahora, ahora, estado, error_log),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ── API pública ───────────────────────────────────────────────────────────────

def generar_fo_in_17(
    docente: dict,
    semestre: str | None = None,
    docente_objetivo: dict | None = None,
) -> dict:
    """
    Genera o refresca el FO-IN-17 del docente para el semestre indicado.

    Si el semestre no se especifica usa el semestre académico actual.
    Si ya existe un registro válido (< 15 días) y NO se pidió un docente objetivo
    distinto, lo retorna directamente sin hacer scraping adicional.

    `docente_objetivo` (opcional): docente cuyos proyectos se piden realmente
    (ej. el usuario pide "los proyectos de Ana Gissele"). Cuando se proporciona,
    se ignora la caché y se regenera, ya que el contenido depende de ese nombre.

    Retorna dict con:
      - pdf_nombre: nombre del archivo PDF para construir el enlace de descarga
      - pdf_path: ruta web relativa del archivo PDF (/static/generados/...)
      - datos: dict con proyectos y metadatos de extracción
      - registro: fila completa de la BD
      - advertencia (opcional): mensaje si se usó versión previa por error de refresco
    """
    if semestre is None:
        semestre, _, _ = calcular_periodo()

    docente_id: int = docente["id"]

    registro = obtener_registro(docente_id, semestre)
    if (
        docente_objetivo is None
        and registro and registro.get("estado") == "ok"
        and not _necesita_refresco(registro.get("fecha_refresco"))
    ):
        logger.info(
            "FO-IN-17: registro vigente para docente %d semestre %s",
            docente_id, semestre,
        )
        datos = json.loads(registro["datos_json"])
        pdf_path = registro["pdf_path"]
        return {
            "registro": registro,
            "pdf_path": pdf_path,
            "pdf_nombre": Path(pdf_path).name if pdf_path else None,
            "datos": datos,
        }

    logger.info(
        "FO-IN-17: extrayendo datos frescos — docente '%s', objetivo '%s', semestre %s",
        docente.get("nombre"),
        (docente_objetivo or {}).get("nombre"),
        semestre,
    )
    try:
        resultado = extraer_proyectos(docente, docente_objetivo=docente_objetivo)
        resultado["semestre_destino"] = semestre

        # Filtrar claves de runtime antes de persistir
        datos_para_guardar = {k: v for k, v in resultado.items() if not k.startswith("_")}
        datos_json_str = json.dumps(datos_para_guardar, ensure_ascii=False)
        fuentes_str = json.dumps(resultado.get("fuentes_consultadas", []), ensure_ascii=False)

        pdf_path = generar_pdf_fo_in_17_plantilla(resultado)
        # Guardar SIEMPRE ruta web relativa (portable), no la ruta absoluta local
        pdf_nombre = Path(pdf_path).name
        pdf_web = f"/static/generados/{pdf_nombre}"

        _guardar_registro(
            docente_id=docente_id,
            semestre=semestre,
            datos_json=datos_json_str,
            pdf_path=pdf_web,
            fuentes_usadas=fuentes_str,
            estado="ok",
        )

        return {
            "registro": obtener_registro(docente_id, semestre),
            "pdf_path": pdf_web,
            "pdf_nombre": pdf_nombre,
            "datos": datos_para_guardar,
        }

    except Exception as exc:
        logger.error("FO-IN-17: error generando documento: %s", exc)

        # Conservar la última versión válida si existe
        if registro and registro.get("estado") == "ok" and registro.get("pdf_path"):
            _guardar_registro(
                docente_id=docente_id,
                semestre=semestre,
                datos_json=registro["datos_json"],
                pdf_path=registro["pdf_path"],
                fuentes_usadas=registro.get("fuentes_usadas", "[]"),
                estado="error_refresco",
                error_log=str(exc),
            )
            datos = json.loads(registro["datos_json"])
            return {
                "registro": obtener_registro(docente_id, semestre),
                "pdf_path": registro["pdf_path"],
                "pdf_nombre": Path(registro["pdf_path"]).name,
                "datos": datos,
                "advertencia": f"Se usó la última versión válida. Error al refrescar: {exc}",
            }

        _guardar_registro(
            docente_id=docente_id,
            semestre=semestre,
            datos_json="{}",
            pdf_path="",
            fuentes_usadas="[]",
            estado="error",
            error_log=str(exc),
        )
        raise


def refrescar_todos(semestre: str | None = None) -> list[dict]:
    """
    Refresca los registros FO-IN-17 con más de INTERVALO_REFRESCO_DIAS días
    sin actualizar. Se llama desde el job periódico.

    Retorna lista con el resultado de cada docente procesado.
    """
    if semestre is None:
        semestre, _, _ = calcular_periodo()

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT * FROM docentes")
        docentes = [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
        conn.close()

    resultados: list[dict] = []
    for docente in docentes:
        registro = obtener_registro(docente["id"], semestre)
        if registro and not _necesita_refresco(registro.get("fecha_refresco")):
            continue
        try:
            res = generar_fo_in_17(docente, semestre)
            resultados.append({"docente": docente["nombre"], "estado": "ok",
                                "pdf_nombre": res.get("pdf_nombre")})
        except Exception as exc:
            resultados.append({"docente": docente["nombre"], "estado": "error", "error": str(exc)})

    return resultados
