"""
Resolución difusa de nombres de docentes del GIA mencionados en el chat normal.

El flujo de entrega de PDFs (routes/chat.py) ya no filtra por docente
individual porque el FO-IN-17 es grupal, pero el chat normal (RAG + Gemini)
sigue necesitando resolver nombres mal escritos (ej. "Freddy" vs "Fredy")
para que el RAG recupere los chunks correctos y Gemini no confunda a dos
docentes con nombres parecidos (ej. "Vera").
"""
from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher

from services.extractor_proyectos import _normalizar_nombre, _obtener_docentes_cvlac

logger = logging.getLogger(__name__)

_UMBRAL_SIMILITUD = 0.84
_TTL_ROSTER_SEGUNDOS = 24 * 3600

# Palabras a ignorar al tokenizar mensaje y nombres (ruido, no identifican a nadie)
_STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "grupo", "gia", "docente", "profe",
    "profesor", "profesora", "investigador", "investigadora", "proyectos",
    "proyecto", "informe", "plan", "accion", "gestion", "para", "con", "que",
    "trabajados", "trabajado", "por", "una", "uno",
}

_roster_cache: list[tuple[str, str]] = []
_roster_cache_ts: float = 0.0


def _obtener_roster() -> list[tuple[str, str]]:
    """Roster de (nombre, cvlac_url) cacheado en módulo con TTL de 24h."""
    global _roster_cache, _roster_cache_ts
    ahora = time.time()
    if _roster_cache and (ahora - _roster_cache_ts) < _TTL_ROSTER_SEGUNDOS:
        return _roster_cache
    try:
        _roster_cache = _obtener_docentes_cvlac()
        _roster_cache_ts = ahora
    except Exception as exc:
        logger.warning("docentes_matcher: no se pudo refrescar el roster: %s", exc)
    return _roster_cache


def _tokenizar(texto: str) -> set[str]:
    return {
        t for t in _normalizar_nombre(texto or "").split()
        if len(t) >= 3 and t not in _STOPWORDS
    }


def _token_coincide(token_msg: str, token_nombre: str) -> bool:
    if token_msg == token_nombre:
        return True
    return SequenceMatcher(None, token_msg, token_nombre).ratio() >= _UMBRAL_SIMILITUD


def resolver_docentes(mensaje: str) -> list[dict]:
    """
    Busca menciones de docentes del GIA en `mensaje` tolerando errores de
    tipeo (1-2 letras de diferencia vía difflib).

    Retorna una lista de dicts {"nombre", "cvlac_url", "hits", "ambiguo",
    "tokens"}. Vacía si no hay coincidencias. Si el docente con más tokens
    coincidentes es único, la lista tiene un solo elemento con
    ambiguo=False. Si hay empate en el máximo de coincidencias (ej. "Vera"
    entre dos docentes), se devuelven todos los empatados con ambiguo=True
    para que el llamador pida aclaración en vez de asumir.
    """
    tokens_msg = _tokenizar(mensaje)
    if not tokens_msg:
        return []

    candidatos: list[tuple[dict, int]] = []
    for nombre, url in _obtener_roster():
        tokens_nombre = _tokenizar(nombre)
        coincididos = {
            tm for tm in tokens_msg
            if any(_token_coincide(tm, tn) for tn in tokens_nombre)
        }
        if coincididos:
            candidatos.append((
                {"nombre": nombre, "cvlac_url": url, "tokens": sorted(coincididos)},
                len(coincididos),
            ))

    if not candidatos:
        return []

    max_hits = max(h for _, h in candidatos)
    empatados = [d for d, h in candidatos if h == max_hits]
    ambiguo = len(empatados) > 1

    return [{**d, "hits": max_hits, "ambiguo": ambiguo} for d in empatados]
