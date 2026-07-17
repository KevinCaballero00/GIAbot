import hashlib
import hmac

import bcrypt

from models.database import get_connection, get_cursor


def hash_password(password: str) -> str:
    """
    Genera el hash de la contraseña para almacenar en BD.

    Usamos bcrypt (con salt aleatorio y factor de coste). El hash resultante
    empieza por ``$2b$`` y es autocontenido, así que ``verificar_password`` lo
    detecta automáticamente.
    """
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verificar_password(password: str, hash_guardado: str) -> bool:
    """
    Compara una contraseña en claro contra el hash almacenado.

    Soporta ambos esquemas para no romper usuarios existentes:
    - bcrypt (``$2a$``/``$2b$``/``$2y$``): se usa ``bcrypt.checkpw``. El salt
      va dentro del propio hash, por eso NO se puede comparar dentro del SQL.
    - SHA-256 heredado (64 caracteres hexadecimales): comparación directa.
    """
    if not hash_guardado:
        return False

    if hash_guardado.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(password.encode(), hash_guardado.encode())
        except ValueError:
            return False

    # Formato heredado SHA-256
    sha = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(sha, hash_guardado)


def verificar_credenciales(usuario: str, password: str) -> dict | None:
    """
    Verifica usuario y contraseña contra la BD.
    Retorna el docente si es válido, None si no.
    """
    conn = get_connection()
    cur = get_cursor(conn)
    try:
        cur.execute(
            "SELECT * FROM docentes WHERE usuario = %s",
            (usuario.strip(),),
        )
        docente = cur.fetchone()
    finally:
        cur.close()
        conn.close()

    if docente and verificar_password(password.strip(), docente["password"]):
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
