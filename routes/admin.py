"""
Rutas administrativas de GIAbot.

Todas las rutas requieren autenticación de docente mediante usuario/contraseña
en el cuerpo de la petición o como query params (según el método HTTP).

Endpoints:
  GET  /admin/proyectos/pendientes     — lista proyectos pendientes de revisión
  GET  /admin/proyectos                — lista todos los proyectos (con filtro opcional)
  POST /admin/proyectos/{id}/aprobar   — aprueba un proyecto pendiente
  POST /admin/proyectos/{id}/rechazar  — rechaza un proyecto pendiente
  POST /admin/refresh-context          — recarga el contexto web y los chunks RAG
  GET  /metrics/export                 — exporta logs de conversación y métricas
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.auth_service import verificar_credenciales

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Modelos de entrada ────────────────────────────────────────────────────────

class CredencialesBase(BaseModel):
    usuario: str
    password: str


class RevisionProyecto(BaseModel):
    usuario: str
    password: str
    notas: str = ""


# ── Helper de autenticación ───────────────────────────────────────────────────

def _autenticar_o_401(usuario: str, password: str) -> dict:
    docente = verificar_credenciales(usuario, password)
    if not docente:
        raise HTTPException(status_code=401, detail="Credenciales inválidas.")
    return docente


# ── Proyectos pendientes ──────────────────────────────────────────────────────

@router.get("/admin/proyectos/pendientes")
def listar_pendientes(
    usuario: str = Query(..., description="Usuario docente"),
    password: str = Query(..., description="Contraseña del docente"),
):
    """Lista los proyectos con estado 'pendiente_revision'."""
    _autenticar_o_401(usuario, password)
    try:
        from services.proyecto_service import obtener_pendientes
        proyectos = obtener_pendientes()
        return {"proyectos": proyectos, "total": len(proyectos)}
    except Exception as exc:
        logger.error("Admin: error al listar pendientes: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/admin/proyectos")
def listar_proyectos(
    usuario: str = Query(...),
    password: str = Query(...),
    estado: str | None = Query(None, description="Filtrar por estado"),
):
    """Lista todos los proyectos, opcionalmente filtrados por estado."""
    _autenticar_o_401(usuario, password)
    try:
        from services.proyecto_service import obtener_todos
        proyectos = obtener_todos(estado=estado)
        return {"proyectos": proyectos, "total": len(proyectos)}
    except Exception as exc:
        logger.error("Admin: error al listar proyectos: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Aprobar / rechazar ────────────────────────────────────────────────────────

@router.post("/admin/proyectos/{proyecto_id}/aprobar")
def aprobar_proyecto(proyecto_id: int, body: RevisionProyecto):
    """Aprueba un proyecto pendiente de revisión."""
    docente = _autenticar_o_401(body.usuario, body.password)
    try:
        from services.proyecto_service import aprobar_proyecto as _aprobar
        actualizado = _aprobar(proyecto_id, docente["id"], body.notas)
        if not actualizado:
            raise HTTPException(
                status_code=404,
                detail="Proyecto no encontrado o ya no está pendiente.",
            )
        return {"ok": True, "mensaje": f"Proyecto {proyecto_id} aprobado."}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Admin: error al aprobar proyecto %d: %s", proyecto_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/admin/proyectos/{proyecto_id}/rechazar")
def rechazar_proyecto(proyecto_id: int, body: RevisionProyecto):
    """Rechaza un proyecto pendiente de revisión."""
    docente = _autenticar_o_401(body.usuario, body.password)
    try:
        from services.proyecto_service import rechazar_proyecto as _rechazar
        actualizado = _rechazar(proyecto_id, docente["id"], body.notas)
        if not actualizado:
            raise HTTPException(
                status_code=404,
                detail="Proyecto no encontrado o ya no está pendiente.",
            )
        return {"ok": True, "mensaje": f"Proyecto {proyecto_id} rechazado."}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Admin: error al rechazar proyecto %d: %s", proyecto_id, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Refresco de contexto ──────────────────────────────────────────────────────

@router.post("/admin/refresh-context")
def refresh_context(body: CredencialesBase):
    """
    Descarga el contexto web del GIA, regenera el caché y repuebla los
    knowledge_chunks (RAG). Operación costosa (~30-60 s en primera ejecución).
    """
    _autenticar_o_401(body.usuario, body.password)
    try:
        from services.ai_service import refrescar_contexto
        mensaje = refrescar_contexto()
        return {"ok": True, "mensaje": mensaje}
    except Exception as exc:
        logger.error("Admin: error al refrescar contexto: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Métricas y exportación ────────────────────────────────────────────────────

@router.get("/metrics/export")
def exportar_metricas(
    usuario: str = Query(...),
    password: str = Query(...),
    formato: str = Query("resumen", description="'resumen' para métricas o 'logs' para registros brutos"),
    limite: int = Query(500, ge=1, le=5000),
):
    """
    Exporta métricas y logs de conversación para validación académica.

    - formato='resumen': retorna KPIs agregados (tasa éxito, tiempos, intenciones).
    - formato='logs': retorna los últimos `limite` registros individuales.
    """
    _autenticar_o_401(usuario, password)
    try:
        from services.log_service import obtener_metricas, exportar_logs
        if formato == "logs":
            datos = exportar_logs(limite=limite)
            return {"logs": datos, "total": len(datos)}
        metricas = obtener_metricas()
        return metricas
    except Exception as exc:
        logger.error("Admin: error al exportar métricas: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
