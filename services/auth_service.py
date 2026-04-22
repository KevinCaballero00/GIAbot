import hashlib
from models.database import get_connection


def hash_password(password: str) -> str:
    """Genera hash SHA-256 de la contraseña."""
    return hashlib.sha256(password.encode()).hexdigest()


def verificar_credenciales(usuario: str, password: str) -> dict | None:
    """
    Verifica usuario y contraseña contra la BD.
    Retorna el docente si es válido, None si no.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM docentes WHERE usuario = ? AND password = ?",
        (usuario.strip(), hash_password(password.strip()))
    )
    docente = cursor.fetchone()
    conn.close()

    if docente:
        return {"id": docente["id"], "nombre": docente["nombre"], "usuario": docente["usuario"]}
    return None


def agregar_docente(nombre: str, usuario: str, password: str):
    """Agrega un nuevo docente a la BD."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO docentes (nombre, usuario, password) VALUES (?, ?, ?)",
            (nombre, usuario, hash_password(password))
        )
        conn.commit()
        print(f"✅ Docente '{nombre}' agregado correctamente.")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        conn.close()