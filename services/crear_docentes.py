from models.database import init_db
from services.auth_service import agregar_docente

def crear_docentes():

    # Inicializar BD
    init_db()

    # ── Agrega aquí los docentes ─────────────────────

    agregar_docente(
        nombre="Fredy Vera Rivera",
        usuario="fvera",
        password="clave123"
    )

    agregar_docente(
        nombre="Eduard Puerto Cuadros",
        usuario="epuerto",
        password="clave456"
    )

    print("\n✅ Docentes registrados en la base de datos.")