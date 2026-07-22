"""
Servicio de logging de conversaciones para validación académica.

Registra cada interacción del chat con métricas de tiempo, intención detectada
y fuentes usadas. Permite exportar datos para calcular precisión, tiempo de
respuesta y tasa de éxito según los criterios del anteproyecto.
"""
from __future__ import annotations

import logging
from datetime import datetime

from models.database import get_connection, get_cursor

logger = logging.getLogger(__name__)


def registrar_log(
    session_id: str,
    mensaje_usuario: str,
    respuesta_bot: str,
    intencion_detectada: str = "chat_normal",
    fuentes_usadas: str = "",
    tiempo_respuesta_ms: int = 0,
    exito: bool = True,
    docente_id: int | None = None,
) -> None:
    """
    Registra una interacción en conversation_logs.
    Los errores no se propagan para no interrumpir el flujo del chat.
    """
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        try:
            cur.execute(
                """
                INSERT INTO conversation_logs
                  (session_id, docente_id, mensaje_usuario, respuesta_bot,
                   intencion_detectada, fuentes_usadas, tiempo_respuesta_ms,
                   exito, fecha)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    docente_id,
                    mensaje_usuario[:2000],
                    respuesta_bot[:4000],
                    intencion_detectada,
                    fuentes_usadas[:500],
                    tiempo_respuesta_ms,
                    exito,
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        logger.warning("Log: no se pudo registrar interacción: %s", exc)


def obtener_metricas() -> dict:
    """
    Calcula métricas de validación académica desde conversation_logs:
      - total de interacciones
      - tasa de éxito
      - tiempo promedio de respuesta (ms)
      - distribución de intenciones detectadas
      - conteo por día (últimos 30)
    """
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        try:
            cur.execute("SELECT COUNT(*) AS total FROM conversation_logs")
            total = cur.fetchone()["total"] or 0

            cur.execute(
                "SELECT COUNT(*) AS exitosas FROM conversation_logs WHERE exito = TRUE"
            )
            exitosas = cur.fetchone()["exitosas"] or 0

            cur.execute(
                "SELECT AVG(tiempo_respuesta_ms) AS promedio FROM conversation_logs "
                "WHERE tiempo_respuesta_ms > 0"
            )
            fila = cur.fetchone()
            tiempo_promedio = round(float(fila["promedio"] or 0), 1)

            cur.execute(
                """
                SELECT intencion_detectada, COUNT(*) AS cantidad
                FROM conversation_logs
                GROUP BY intencion_detectada
                ORDER BY cantidad DESC
                """
            )
            intenciones = {r["intencion_detectada"]: r["cantidad"] for r in cur.fetchall()}

            cur.execute(
                """
                SELECT LEFT(fecha, 10) AS dia, COUNT(*) AS cantidad
                FROM conversation_logs
                WHERE fecha >= NOW()::TEXT::DATE::TEXT - INTERVAL '29 days'
                GROUP BY dia
                ORDER BY dia DESC
                """
            )
            por_dia = {r["dia"]: r["cantidad"] for r in cur.fetchall()}

            return {
                "total_interacciones": total,
                "tasa_exito_pct": round(exitosas / total * 100, 1) if total else 0.0,
                "tiempo_promedio_ms": tiempo_promedio,
                "intenciones": intenciones,
                "por_dia": por_dia,
            }
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        logger.error("Log: error al calcular métricas: %s", exc)
        return {"error": str(exc)}


def exportar_logs(limite: int = 1000) -> list[dict]:
    """Retorna los últimos `limite` logs para exportar a CSV/JSON."""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        try:
            cur.execute(
                """
                SELECT id, session_id, docente_id, mensaje_usuario, respuesta_bot,
                       intencion_detectada, fuentes_usadas, tiempo_respuesta_ms,
                       exito, fecha
                FROM conversation_logs
                ORDER BY fecha DESC
                LIMIT %s
                """,
                (limite,),
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        logger.error("Log: error al exportar logs: %s", exc)
        return []
