import hashlib

from models.database import get_connection, get_cursor


def hash_password(password: str) -> str:
    """Genera hash SHA-256 de la contraseña."""
    return hashlib.sha256(password.encode()).hexdigest()


def verificar_credenciales(usuario: str, password: str) -> dict | None:
    """
    Verifica usuario y contraseña contra la BD.
    Retorna el docente si es válido, None si no.
    """
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT * FROM docentes WHERE usuario = %s AND password = %s",
            (usuario.strip(), hash_password(password.strip())),
        )
        docente = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if docente:
        return {"id": docente["id"], "nombre": docente["nombre"], "usuario": docente["usuario"]}
    return None


def agregar_docente(nombre: str, usuario: str, password: str):
    """Agrega un nuevo docente a la BD."""
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            """
            INSERT INTO docentes (nombre, usuario, password)
            VALUES (%s, %s, %s)
            ON CONFLICT (usuario) DO NOTHING
            """,
            (nombre, usuario, hash_password(password)),
        )
        conn.commit()
        if cur.rowcount > 0:
            print(f"[OK] Docente '{nombre}' agregado correctamente.")
    except Exception as e:
        conn.rollback()
        print(f"[ERROR] {e}")
    finally:
        cur.close()
        conn.close()
