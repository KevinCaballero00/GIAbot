"""
Servicio de gestión de proyectos registrados conversacionalmente.

Los proyectos se cargan por chat con estado 'pendiente_revision'.
Los docentes autenticados los revisan y cambian a 'aprobado' o 'rechazado'
antes de que sean incluidos en respuestas o reportes del bot.
"""
from __future__ import annotations

import logging
from datetime import datetime

from models.database import get_connection, get_cursor

logger = logging.getLogger(__name__)


def registrar_proyecto(
    docente_id: int | None,
    titulo: str,
    linea: str = "",
    objetivo: str = "",
    actividades: str = "",
    responsable: str = "",
    producto: str = "",
    periodo: str = "",
    fuente: str = "conversacional",
) -> dict:
    """
    Guarda un nuevo proyecto con estado 'pendiente_revision'.
    Retorna el dict del proyecto creado (con su id).
    """
    fecha = datetime.utcnow().isoformat()
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO proyectos
              (docente_id, titulo, linea, objetivo, actividades, responsable,
               producto, periodo, fuente, estado, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pendiente_revision', %s)
            RETURNING *
            """,
            (docente_id, titulo, linea, objetivo, actividades, responsable,
             producto, periodo, fuente, fecha),
        )
        proyecto = dict(cur.fetchone())
        conn.commit()
        logger.info("Proyecto registrado con id=%d por docente_id=%s", proyecto["id"], docente_id)
        return proyecto
    except Exception as exc:
        conn.rollback()
        logger.error("Error al registrar proyecto: %s", exc)
        raise
    finally:
        cur.close()
        conn.close()


def obtener_pendientes() -> list[dict]:
    """Retorna todos los proyectos con estado 'pendiente_revision'."""
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            SELECT p.*, d.nombre AS nombre_docente
            FROM proyectos p
            LEFT JOIN docentes d ON d.id = p.docente_id
            WHERE p.estado = 'pendiente_revision'
            ORDER BY p.fecha_registro DESC
            """
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Error al obtener proyectos pendientes: %s", exc)
        return []
    finally:
        cur.close()
        conn.close()


def obtener_todos(estado: str | None = None) -> list[dict]:
    """Retorna proyectos filtrando por estado (o todos si estado es None)."""
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        if estado:
            cur.execute(
                """
                SELECT p.*, d.nombre AS nombre_docente
                FROM proyectos p
                LEFT JOIN docentes d ON d.id = p.docente_id
                WHERE p.estado = %s
                ORDER BY p.fecha_registro DESC
                """,
                (estado,),
            )
        else:
            cur.execute(
                """
                SELECT p.*, d.nombre AS nombre_docente
                FROM proyectos p
                LEFT JOIN docentes d ON d.id = p.docente_id
                ORDER BY p.fecha_registro DESC
                """
            )
        return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Error al obtener proyectos: %s", exc)
        return []
    finally:
        cur.close()
        conn.close()


def _cambiar_estado(
    proyecto_id: int,
    nuevo_estado: str,
    aprobado_por_id: int,
    notas: str = "",
) -> bool:
    """Cambia el estado de un proyecto. Retorna True si tuvo efecto."""
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            UPDATE proyectos
            SET estado = %s,
                fecha_aprobacion = %s,
                aprobado_por = %s,
                notas_revision = %s
            WHERE id = %s AND estado = 'pendiente_revision'
            """,
            (nuevo_estado, datetime.utcnow().isoformat(), aprobado_por_id, notas, proyecto_id),
        )
        actualizado = cur.rowcount > 0
        conn.commit()
        return actualizado
    except Exception as exc:
        conn.rollback()
        logger.error("Error al cambiar estado proyecto %d: %s", proyecto_id, exc)
        return False
    finally:
        cur.close()
        conn.close()


def aprobar_proyecto(proyecto_id: int, aprobado_por_id: int, notas: str = "") -> bool:
    """Aprueba un proyecto pendiente."""
    return _cambiar_estado(proyecto_id, "aprobado", aprobado_por_id, notas)


def rechazar_proyecto(proyecto_id: int, aprobado_por_id: int, notas: str = "") -> bool:
    """Rechaza un proyecto pendiente."""
    return _cambiar_estado(proyecto_id, "rechazado", aprobado_por_id, notas)
