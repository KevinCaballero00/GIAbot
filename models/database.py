import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def get_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    """Crea las tablas necesarias si no existen."""
    conn = get_connection()
    cur = get_cursor(conn)
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
        conn.commit()
    finally:
        cur.close()
        conn.close()


# Se inicializa al importar
init_db()
