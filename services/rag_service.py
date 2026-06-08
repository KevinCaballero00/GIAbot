"""
Servicio RAG (Retrieval-Augmented Generation).

Divide el contexto web del GIA en fragmentos (chunks), los persiste en
knowledge_chunks y los recupera por relevancia usando búsqueda de texto
completo de PostgreSQL (configuración 'spanish').

API pública:
  poblar_chunks(contexto_web)  — reconstruye los chunks desde el contexto
  buscar_contexto_relevante(query, top_k) — recupera contexto pertinente
  guardar_reporte(...)         — registra un reporte PDF generado
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from models.database import get_connection, get_cursor

logger = logging.getLogger(__name__)

_MAX_CHUNK_CHARS = 1200
_MIN_CHUNK_CHARS = 80


# ── Helpers de chunking ───────────────────────────────────────────────────────

def _dividir_en_chunks(texto: str, fuente: str, url: str | None = None) -> list[dict]:
    """Divide texto en bloques de tamaño razonable para el RAG."""
    parrafos = re.split(r"\n{2,}", texto.strip())
    chunks: list[dict] = []
    buf = ""

    for parrafo in parrafos:
        parrafo = parrafo.strip()
        if not parrafo or len(parrafo) < 10:
            continue
        if len(buf) + len(parrafo) < _MAX_CHUNK_CHARS:
            buf = (buf + "\n\n" + parrafo).strip()
        else:
            if len(buf) >= _MIN_CHUNK_CHARS:
                chunks.append({"fuente": fuente, "url": url, "contenido": buf})
            buf = parrafo

    if len(buf) >= _MIN_CHUNK_CHARS:
        chunks.append({"fuente": fuente, "url": url, "contenido": buf})

    return chunks


def _parsear_secciones(contexto_web: str) -> list[dict]:
    """Separa el contexto web en secciones por página y las divide en chunks."""
    chunks_todos: list[dict] = []
    secciones = re.split(r"--- Página: (https?://[^\s]+) ---", contexto_web)

    i = 1
    while i < len(secciones) - 1:
        url = secciones[i].strip()
        contenido = secciones[i + 1]
        chunks = _dividir_en_chunks(contenido, fuente=f"GIA Web — {url}", url=url)
        chunks_todos.extend(chunks)
        i += 2

    if not chunks_todos:
        chunks = _dividir_en_chunks(contexto_web, fuente="GIA Web")
        chunks_todos.extend(chunks)

    return chunks_todos


# ── Persistencia ──────────────────────────────────────────────────────────────

def poblar_chunks(contexto_web: str) -> int:
    """
    Vacía knowledge_chunks y lo repuebla desde contexto_web.
    Retorna el número de chunks insertados.
    """
    if not contexto_web or not contexto_web.strip():
        return 0

    chunks = _parsear_secciones(contexto_web)
    if not chunks:
        return 0

    fecha = datetime.utcnow().isoformat()

    try:
        conn = get_connection()
        cur = get_cursor(conn)
        try:
            cur.execute("DELETE FROM knowledge_chunks")
            for ch in chunks:
                cur.execute(
                    """
                    INSERT INTO knowledge_chunks (fuente, url, contenido, fecha_extraccion)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (ch["fuente"], ch.get("url"), ch["contenido"], fecha),
                )
            conn.commit()
            logger.info("RAG: %d chunks insertados en knowledge_chunks.", len(chunks))
            return len(chunks)
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        logger.error("RAG: error al poblar chunks: %s", exc)
        return 0


# ── Búsqueda ──────────────────────────────────────────────────────────────────

def buscar_contexto_relevante(query: str, top_k: int = 5) -> str:
    """
    Recupera los chunks más relevantes para `query` usando búsqueda de texto
    completo PostgreSQL + fallback ILIKE.

    Incluye también proyectos aprobados que coincidan con la consulta.
    Retorna string con el contexto concatenado listo para inyectar al LLM.
    """
    if not query or not query.strip():
        return ""

    fragmentos: list[str] = []

    try:
        conn = get_connection()
        cur = get_cursor(conn)
        try:
            # Búsqueda full-text (PostgreSQL Spanish)
            cur.execute(
                """
                SELECT contenido, fuente
                FROM knowledge_chunks
                WHERE to_tsvector('spanish', contenido) @@
                      plainto_tsquery('spanish', %s)
                ORDER BY
                    ts_rank(to_tsvector('spanish', contenido),
                            plainto_tsquery('spanish', %s)) DESC
                LIMIT %s
                """,
                (query, query, top_k),
            )
            filas = cur.fetchall()

            # Fallback ILIKE si FTS no devuelve resultados
            if not filas:
                palabras = [p for p in query.split() if len(p) > 3]
                if palabras:
                    patron = "%" + palabras[0] + "%"
                    cur.execute(
                        """
                        SELECT contenido, fuente
                        FROM knowledge_chunks
                        WHERE contenido ILIKE %s
                        LIMIT %s
                        """,
                        (patron, top_k),
                    )
                    filas = cur.fetchall()

            for fila in filas:
                fragmentos.append(
                    f"[Fuente: {fila['fuente']}]\n{fila['contenido']}"
                )

            # Proyectos aprobados relevantes
            cur.execute(
                """
                SELECT titulo, linea, objetivo, actividades, responsable, producto, periodo
                FROM proyectos
                WHERE estado = 'aprobado'
                  AND (titulo ILIKE %s OR objetivo ILIKE %s OR linea ILIKE %s)
                LIMIT 5
                """,
                (f"%{query}%", f"%{query}%", f"%{query}%"),
            )
            proyectos = cur.fetchall()
            for p in proyectos:
                bloque = (
                    f"[Proyecto aprobado GIA — {p['periodo'] or 'período actual'}]\n"
                    f"Título: {p['titulo']}\n"
                    f"Línea: {p['linea'] or ''}\n"
                    f"Objetivo: {p['objetivo'] or ''}\n"
                    f"Responsable: {p['responsable'] or ''}\n"
                    f"Producto: {p['producto'] or ''}"
                )
                fragmentos.append(bloque)

        finally:
            cur.close()
            conn.close()

    except Exception as exc:
        logger.warning("RAG: error al buscar contexto: %s", exc)

    if not fragmentos:
        return ""

    return (
        "\n\n=== CONTEXTO RECUPERADO (RAG) ===\n\n"
        + "\n\n---\n\n".join(fragmentos)
        + "\n\n=== FIN CONTEXTO RAG ===\n"
    )


# ── Registro de reportes generados ───────────────────────────────────────────

def guardar_reporte(
    docente_id: int | None,
    tipo: str,
    semestre: str,
    pdf_path: str,
    fuentes_usadas: str = "[]",
) -> None:
    """Registra en BD que se generó un reporte PDF."""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
        try:
            cur.execute(
                """
                INSERT INTO reportes_generados
                  (docente_id, tipo, semestre, pdf_path, fuentes_usadas, fecha_generacion)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (docente_id, tipo, semestre, pdf_path, fuentes_usadas,
                 datetime.utcnow().isoformat()),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
    except Exception as exc:
        logger.warning("RAG: no se pudo guardar registro de reporte: %s", exc)
