import logging
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """Crea las tablas necesarias si no existen. No lanza excepción al fallar."""
    try:
        conn = get_connection()
        cur = get_cursor(conn)
    except Exception as exc:
        logger.error("BD: no se pudo conectar para inicializar tablas: %s", exc)
        return

    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS docentes (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                usuario TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fo_in_17 (
                id SERIAL PRIMARY KEY,
                docente_id INTEGER NOT NULL,
                semestre TEXT NOT NULL,
                datos_json TEXT,
                pdf_path TEXT,
                fuentes_usadas TEXT,
                fecha_creacion TEXT NOT NULL,
                fecha_refresco TEXT,
                estado TEXT DEFAULT 'pendiente',
                error_log TEXT,
                UNIQUE(docente_id, semestre),
                FOREIGN KEY (docente_id) REFERENCES docentes(id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS proyectos (
                id SERIAL PRIMARY KEY,
                docente_id INTEGER REFERENCES docentes(id),
                titulo TEXT NOT NULL,
                linea TEXT,
                objetivo TEXT,
                actividades TEXT,
                responsable TEXT,
                producto TEXT,
                periodo TEXT,
                fuente TEXT DEFAULT 'conversacional',
                estado TEXT DEFAULT 'pendiente_revision',
                fecha_registro TEXT NOT NULL,
                fecha_aprobacion TEXT,
                aprobado_por INTEGER REFERENCES docentes(id),
                notas_revision TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id SERIAL PRIMARY KEY,
                fuente TEXT NOT NULL,
                url TEXT,
                titulo TEXT,
                contenido TEXT NOT NULL,
                fecha_extraccion TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reportes_generados (
                id SERIAL PRIMARY KEY,
                docente_id INTEGER REFERENCES docentes(id),
                tipo TEXT NOT NULL,
                semestre TEXT NOT NULL,
                pdf_path TEXT,
                fuentes_usadas TEXT,
                fecha_generacion TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversation_logs (
                id SERIAL PRIMARY KEY,
                session_id TEXT,
                docente_id INTEGER,
                mensaje_usuario TEXT,
                respuesta_bot TEXT,
                intencion_detectada TEXT,
                fuentes_usadas TEXT,
                tiempo_respuesta_ms INTEGER,
                exito BOOLEAN DEFAULT TRUE,
                fecha TEXT NOT NULL
            )
        """)
        conn.commit()
        logger.info("BD: esquema verificado/inicializado correctamente.")
    except Exception as exc:
        logger.error("BD: error al crear tablas: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        cur.close()
        conn.close()


# Se inicializa al importar
init_db()
