"""
Servicio de gestión del FO-IN-17.

FO-IN-17 es el documento fuente del semestre actual:
  - Se extrae de CvLAC y Google Scholar (via extractor_proyectos)
  - Se normaliza a JSON
  - Se persiste en la base de datos indexado por (responsable_nombre, semestre)
  - Se refresca automáticamente si han pasado más de 15 días

La tabla `docentes` identifica quién tiene permiso para generar el documento
(generado_por_docente_id), no de quién son los proyectos (responsable_nombre).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from models.database import get_connection, get_cursor
from services.extractor_proyectos import RESPONSABLE_GRUPAL, calcular_periodo, extraer_proyectos
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

def obtener_registro_por_responsable(responsable_nombre: str, semestre: str) -> dict | None:
    """Obtiene el registro FO-IN-17 por persona real + semestre; retorna None si no existe."""
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT * FROM fo_in_17 WHERE responsable_nombre = %s AND semestre = %s",
            (responsable_nombre, semestre),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def obtener_ultimo_fo_in_17_valido() -> dict | None:
    """
    Retorna el registro FO-IN-17 grupal más reciente con estado='ok'. Es la
    fuente canónica que usa el FO-IN-13.

    El dict incluye `generado_por_nombre` (nombre del docente que lo solicitó)
    obtenido por JOIN con la tabla docentes.
    """
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            SELECT f.*, d.nombre AS generado_por_nombre
            FROM fo_in_17 f
            LEFT JOIN docentes d ON d.id = f.generado_por_docente_id
            WHERE f.estado = 'ok' AND f.responsable_nombre = %s
            ORDER BY COALESCE(f.fecha_refresco, f.fecha_creacion) DESC
            LIMIT 1
            """,
            (RESPONSABLE_GRUPAL,),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
        conn.close()


def _guardar_registro(
    generado_por_docente_id: int,
    responsable_nombre: str,
    semestre: str,
    datos_json: str,
    pdf_path: str,
    fuentes_usadas: str,
    responsable_cvlac_url: str | None = None,
    estado: str = "ok",
    error_log: str | None = None,
) -> None:
    """
    Inserta o actualiza el registro en fo_in_17.

    La clave lógica es (responsable_nombre, semestre): cada persona tiene como
    máximo un registro por semestre. docente_id (= generado_por_docente_id) es
    quien se autenticó y solicitó la generación.
    """
    ahora = datetime.utcnow().isoformat()
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT id FROM fo_in_17 WHERE responsable_nombre = %s AND semestre = %s",
            (responsable_nombre, semestre),
        )
        existe = cur.fetchone()
        if existe:
            cur.execute(
                """
                UPDATE fo_in_17
                SET datos_json = %s, pdf_path = %s, fuentes_usadas = %s,
                    fecha_refresco = %s, estado = %s, error_log = %s,
                    generado_por_docente_id = %s, responsable_cvlac_url = %s
                WHERE responsable_nombre = %s AND semestre = %s
                """,
                (datos_json, pdf_path, fuentes_usadas, ahora, estado, error_log,
                 generado_por_docente_id, responsable_cvlac_url,
                 responsable_nombre, semestre),
            )
        else:
            cur.execute(
                """
                INSERT INTO fo_in_17
                  (docente_id, semestre, datos_json, pdf_path, fuentes_usadas,
                   fecha_creacion, fecha_refresco, estado, error_log,
                   responsable_nombre, responsable_cvlac_url, generado_por_docente_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (generado_por_docente_id, semestre, datos_json, pdf_path, fuentes_usadas,
                 ahora, ahora, estado, error_log,
                 responsable_nombre, responsable_cvlac_url, generado_por_docente_id),
            )
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ── API pública ───────────────────────────────────────────────────────────────

def generar_fo_in_17(
    docente: dict,
    semestre: str | None = None,
) -> dict:
    """
    Genera o refresca el FO-IN-17 grupal (todo el GIA) para el semestre indicado.

    `docente` es el docente autenticado que solicita el documento (se persiste
    como `generado_por_docente_id`); el responsable del documento es siempre
    el grupo GIA, no una persona.

    La caché se busca por (responsable_nombre="GIA", semestre), con el mismo
    índice único que ya existía para el modo por-docente.

    Retorna dict con:
      - pdf_nombre: nombre del archivo PDF
      - pdf_path: ruta web relativa (/static/generados/...)
      - datos: dict con proyectos y metadatos
      - registro: fila completa de la BD
      - advertencia (opcional): mensaje si se usó versión previa por error de refresco
    """
    if semestre is None:
        semestre, _, _ = calcular_periodo()

    docente_id: int = docente["id"]
    responsable_nombre: str = RESPONSABLE_GRUPAL
    responsable_cvlac_url: str | None = None

    registro = obtener_registro_por_responsable(responsable_nombre, semestre)
    if (
        registro and registro.get("estado") == "ok"
        and not _necesita_refresco(registro.get("fecha_refresco"))
    ):
        logger.info(
            "FO-IN-17: registro vigente para responsable '%s', semestre %s",
            responsable_nombre, semestre,
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
        "FO-IN-17: extrayendo datos frescos — generado por '%s', responsable '%s', semestre %s",
        docente.get("nombre"),
        responsable_nombre,
        semestre,
    )
    try:
        resultado = extraer_proyectos(docente)
        resultado["semestre_destino"] = semestre

        datos_para_guardar = {k: v for k, v in resultado.items() if not k.startswith("_")}
        datos_json_str = json.dumps(datos_para_guardar, ensure_ascii=False)
        fuentes_str = json.dumps(resultado.get("fuentes_consultadas", []), ensure_ascii=False)

        pdf_path = generar_pdf_fo_in_17_plantilla(resultado)
        pdf_nombre = Path(pdf_path).name
        pdf_web = f"/static/generados/{pdf_nombre}"

        _guardar_registro(
            generado_por_docente_id=docente_id,
            responsable_nombre=responsable_nombre,
            responsable_cvlac_url=responsable_cvlac_url,
            semestre=semestre,
            datos_json=datos_json_str,
            pdf_path=pdf_web,
            fuentes_usadas=fuentes_str,
            estado="ok",
        )

        return {
            "registro": obtener_registro_por_responsable(responsable_nombre, semestre),
            "pdf_path": pdf_web,
            "pdf_nombre": pdf_nombre,
            "datos": datos_para_guardar,
        }

    except Exception as exc:
        logger.error("FO-IN-17: error generando documento: %s", exc)

        if registro and registro.get("estado") == "ok" and registro.get("pdf_path"):
            _guardar_registro(
                generado_por_docente_id=docente_id,
                responsable_nombre=responsable_nombre,
                responsable_cvlac_url=responsable_cvlac_url,
                semestre=semestre,
                datos_json=registro["datos_json"],
                pdf_path=registro["pdf_path"],
                fuentes_usadas=registro.get("fuentes_usadas", "[]"),
                estado="error_refresco",
                error_log=str(exc),
            )
            datos = json.loads(registro["datos_json"])
            return {
                "registro": obtener_registro_por_responsable(responsable_nombre, semestre),
                "pdf_path": registro["pdf_path"],
                "pdf_nombre": Path(registro["pdf_path"]).name,
                "datos": datos,
                "advertencia": f"Se usó la última versión válida. Error al refrescar: {exc}",
            }

        _guardar_registro(
            generado_por_docente_id=docente_id,
            responsable_nombre=responsable_nombre,
            responsable_cvlac_url=responsable_cvlac_url,
            semestre=semestre,
            datos_json="{}",
            pdf_path="",
            fuentes_usadas="[]",
            estado="error",
            error_log=str(exc),
        )
        raise


def refrescar_grupal(semestre: str | None = None) -> list[dict]:
    """
    Refresca el registro FO-IN-17 grupal si lleva más de INTERVALO_REFRESCO_DIAS
    días sin actualizar. No hace nada si ya está vigente.

    El campo `docente_id` de la tabla es NOT NULL, así que se usa el primer
    docente registrado (por id) como `generado_por` del refresco automático.
    """
    if semestre is None:
        semestre, _, _ = calcular_periodo()

    registro = obtener_registro_por_responsable(RESPONSABLE_GRUPAL, semestre)
    if registro and not _necesita_refresco(registro.get("fecha_refresco")):
        return [{"responsable": RESPONSABLE_GRUPAL, "estado": "vigente"}]

    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute("SELECT * FROM docentes ORDER BY id LIMIT 1")
        row = cur.fetchone()
        generado_por = dict(row) if row else None
    finally:
        cur.close()
        conn.close()

    if not generado_por:
        return [{"responsable": RESPONSABLE_GRUPAL, "estado": "error",
                  "error": "No hay docentes registrados para generar el FO-IN-17"}]

    try:
        res = generar_fo_in_17(generado_por, semestre)
        return [{"responsable": RESPONSABLE_GRUPAL, "estado": "ok",
                  "pdf_nombre": res.get("pdf_nombre")}]
    except Exception as exc:
        return [{"responsable": RESPONSABLE_GRUPAL, "estado": "error", "error": str(exc)}]
