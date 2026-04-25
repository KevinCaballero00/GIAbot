"""
Scraper del sitio web del GIA.

Mejoras frente a la versión inicial:
  * Conserva los enlaces académicos (Google Scholar, ORCID, ResearchGate,
    CvLAC, LinkedIn, GitHub, Scopus, Publons) en línea con el texto, en vez
    de descartarlos con `get_text()`.
  * Para la página `/team/` produce además un bloque estructurado con los
    docentes y todos sus perfiles académicos agrupados por persona.
  * Añade caché en disco con TTL de 24 h para que los reinicios del servidor
    no requieran siempre 7 peticiones HTTP (y permitan trabajar offline si
    ya hubo una corrida exitosa).
  * Headers, timeouts y manejo de errores explícito por página, sin tumbar
    la importación si el sitio está caído.

Mantiene la API pública previa: `CONTEXTO_WEB` queda disponible al importar.
También expone `refrescar_contexto_web()` para forzar una recarga.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://gia.ufps.edu.co"
URLS = [
    f"{BASE_URL}/index/",
    f"{BASE_URL}/team/",
    f"{BASE_URL}/semilleros/",
    f"{BASE_URL}/servicios/",
    f"{BASE_URL}/proyectos/",
    f"{BASE_URL}/contacto/",
    f"{BASE_URL}/about/",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; GIAbot-Scraper/1.1; "
        "+https://gia.ufps.edu.co)"
    )
}

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "contexto_web.json"
CACHE_TTL_SECONDS = 60 * 60 * 24  # 24 h

# Mapa host -> etiqueta legible para clasificar enlaces académicos.
ACADEMIC_HOSTS: dict[str, str] = {
    "scholar.google":       "Google Scholar",
    "scienti.minciencias":  "CvLAC (Minciencias)",
    "researchgate.net":     "ResearchGate",
    "orcid.org":            "ORCID",
    "linkedin.com":         "LinkedIn",
    "github.com":           "GitHub",
    "academia.edu":         "Academia.edu",
    "publons.com":          "Publons",
    "scopus.com":           "Scopus",
}


# ───────────────────────────── utilidades ─────────────────────────────────────

def _clasificar_enlace(url: str) -> str | None:
    """Devuelve la etiqueta del perfil académico, o None si no es académico."""
    u = (url or "").lower().strip()
    if not u:
        return None
    if u.startswith("mailto:"):
        return "Email"
    if u.startswith("tel:"):
        return "Teléfono"
    for host, etiqueta in ACADEMIC_HOSTS.items():
        if host in u:
            return etiqueta
    return None


def _fetch(url: str, timeout: int = 15) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.warning("Scraper: fallo al obtener %s: %s", url, e)
        return None


def _limpiar_dom(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    for sel in ["nav", "footer", "header"]:
        for el in soup.find_all(sel):
            el.decompose()
    for el in soup.select(
        ".cookie, .menu, .nav, .navigation, .site-navigation, .widget"
    ):
        el.decompose()
    return soup


def _inline_links(soup: BeautifulSoup) -> None:
    """Reemplaza cada <a href> por su texto.

    Para enlaces académicos / de contacto conserva la URL en línea, para que
    el modelo pueda citarlos. Para enlaces normales sólo deja el texto.
    """
    for a in list(soup.find_all("a", href=True)):
        href = a["href"].strip()
        text = a.get_text(" ", strip=True)
        if not href or href.startswith("#") or href.startswith("javascript:"):
            a.replace_with(text or "")
            continue
        kind = _clasificar_enlace(href)
        if kind:
            if text and text.lower() != kind.lower():
                a.replace_with(f"[{text} — {kind}: {href}]")
            else:
                a.replace_with(f"[{kind}: {href}]")
        else:
            a.replace_with(text or "")


# ───────────────── extracción estructurada de la página /team/ ────────────────

_DEGREE_RE = re.compile(
    r"\b(Ph\.?\s*D\.?(\(c\))?|M\.?Sc\.?(\(c\))?|Ing\.?|Mg\.?|Dr\.?|Esp\.?)\b",
    re.IGNORECASE,
)


def _docente_desde_card(card) -> dict | None:
    """Extrae nombre/rol/enlaces a partir de un card `.member` del tema actual."""
    nombre = None
    for h in card.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        t = h.get_text(" ", strip=True)
        if t and 5 < len(t) < 140:
            nombre = t
            break
    if not nombre:
        return None

    rol = None
    for p in card.find_all(["p", "span", "em", "i", "small"]):
        t = p.get_text(" ", strip=True)
        if (
            t and t != nombre
            and 3 < len(t) < 250
            and "@" not in t
            and "http" not in t.lower()
        ):
            rol = t
            break

    enlaces: list[dict] = []
    urls_vistas: set[str] = set()
    for a in card.find_all("a", href=True):
        tipo = _clasificar_enlace(a["href"])
        if not tipo:
            continue
        href = a["href"].strip()
        if href in urls_vistas:
            continue
        urls_vistas.add(href)
        enlaces.append({"tipo": tipo, "url": href})

    if not enlaces:
        return None
    return {"nombre": nombre, "rol": rol, "enlaces": enlaces}


def _extraer_docentes(soup: BeautifulSoup) -> list[dict]:
    """Agrupa enlaces académicos por docente.

    Detector primario: `div.member` (estructura del tema actual del sitio).
    Si el tema cambia y no hay coincidencias, cae a la heurística por
    cluster de enlaces académicos.
    """
    docentes: list[dict] = []

    # ── Detector primario por clase del tema ─────────────────────────────────
    for card in soup.select("div.member, .team-member, .member-card"):
        d = _docente_desde_card(card)
        if d:
            docentes.append(d)

    if docentes:
        # Deduplicar por nombre.
        vistos: set[str] = set()
        unicos: list[dict] = []
        for d in docentes:
            if d["nombre"] in vistos:
                continue
            vistos.add(d["nombre"])
            unicos.append(d)
        return unicos

    # ── Fallback heurístico (por si cambia el tema del sitio) ─────────────────
    visitados: set[int] = set()
    anclas_academicas = [
        a for a in soup.find_all("a", href=True)
        if _clasificar_enlace(a["href"])
    ]

    def _buscar_container(ancla, min_links: int):
        c = ancla
        for _ in range(8):
            c = c.parent
            if c is None:
                return None
            hermanos = [
                a for a in c.find_all("a", href=True)
                if _clasificar_enlace(a["href"])
            ]
            if len(hermanos) >= min_links:
                # Y que contenga un heading con marcador de grado académico.
                for h in c.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                    if _DEGREE_RE.search(h.get_text(" ", strip=True) or ""):
                        return c
                if min_links >= 2:
                    return c  # Cluster grande sin grado: aún así lo aceptamos.
        return None

    for ancla in anclas_academicas:
        container = _buscar_container(ancla, min_links=2)
        if container is None:
            # Fallback para docentes con un único perfil (p. ej. sólo CvLAC):
            # exigimos heading con grado para evitar capturar enlaces sueltos.
            container = _buscar_container(ancla, min_links=1)
        if container is None or id(container) in visitados:
            continue
        visitados.add(id(container))

        # Buscar el nombre: heading cercano dentro del contenedor o subiendo.
        nombre = None
        scope = container
        for _ in range(4):
            for h in scope.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                t = h.get_text(" ", strip=True)
                if t and 5 < len(t) < 140 and _DEGREE_RE.search(t):
                    nombre = t
                    break
            if nombre:
                break
            if scope.parent is None:
                break
            scope = scope.parent

        if not nombre:
            # Fallback: cualquier heading cercano legible.
            for h in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                t = h.get_text(" ", strip=True)
                if t and 5 < len(t) < 140:
                    nombre = t
                    break

        if not nombre:
            continue

        # Rol: primer párrafo/span con texto significativo distinto al nombre.
        rol = None
        for p in container.find_all(["p", "span", "em", "i", "small"]):
            t = p.get_text(" ", strip=True)
            if (
                t and t != nombre
                and 3 < len(t) < 250
                and "@" not in t
                and "http" not in t.lower()
            ):
                rol = t
                break

        # Recolectar y deduplicar enlaces académicos del cluster.
        enlaces: list[dict] = []
        urls_vistas: set[str] = set()
        for a in container.find_all("a", href=True):
            tipo = _clasificar_enlace(a["href"])
            if not tipo:
                continue
            href = a["href"].strip()
            if href in urls_vistas:
                continue
            urls_vistas.add(href)
            enlaces.append({"tipo": tipo, "url": href})

        if enlaces:
            docentes.append({"nombre": nombre, "rol": rol, "enlaces": enlaces})

    # Deduplicar docentes por nombre conservando el primero.
    vistos_nombres: set[str] = set()
    unicos: list[dict] = []
    for d in docentes:
        if d["nombre"] in vistos_nombres:
            continue
        vistos_nombres.add(d["nombre"])
        unicos.append(d)
    return unicos


def _formatear_docentes(docentes: list[dict]) -> str:
    if not docentes:
        return ""
    lineas = [
        "### Directorio estructurado de docentes / investigadores del GIA",
        "(Cada docente con todos sus perfiles académicos verificados de la "
        "página oficial. Cita estos enlaces cuando el usuario pregunte por un "
        "investigador específico.)",
    ]
    for d in docentes:
        lineas.append("")
        lineas.append(f"**{d['nombre']}**")
        if d.get("rol"):
            lineas.append(f"- Rol: {d['rol']}")
        for enlace in d["enlaces"]:
            lineas.append(f"- {enlace['tipo']}: {enlace['url']}")
    return "\n".join(lineas)


# ────────────────────────── orquestación por página ──────────────────────────

def _es_pagina_team(url: str) -> bool:
    return urlparse(url).path.rstrip("/").endswith("/team")


def _scrape_pagina(url: str) -> str:
    html = _fetch(url)
    if html is None:
        return (
            f"--- Página: {url} ---\n"
            "[No se pudo obtener el contenido en este momento]\n"
        )

    soup = BeautifulSoup(html, "html.parser")
    _limpiar_dom(soup)

    extras: list[str] = []
    if _es_pagina_team(url):
        docentes = _extraer_docentes(soup)
        bloque = _formatear_docentes(docentes)
        if bloque:
            extras.append(bloque)

    _inline_links(soup)
    texto = soup.get_text(separator="\n", strip=True)
    texto = re.sub(r"\n\s*\n+", "\n\n", texto)

    partes = [f"--- Página: {url} ---", texto]
    partes.extend(extras)
    return "\n".join(partes) + "\n"


def _construir_contexto() -> str:
    return "\n".join(_scrape_pagina(u) for u in URLS)


# ─────────────────────────────── caché en disco ──────────────────────────────

def _leer_cache() -> str | None:
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - data.get("timestamp", 0) > CACHE_TTL_SECONDS:
            return None
        return data.get("contexto")
    except Exception as e:
        logger.warning("Scraper: caché ilegible (%s), se ignora.", e)
        return None


def _guardar_cache(contexto: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(
                {"timestamp": time.time(), "contexto": contexto},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Scraper: no se pudo guardar caché: %s", e)


# ──────────────────────────────── API pública ────────────────────────────────

def obtener_contexto_web(force_refresh: bool = False) -> str:
    """Devuelve el contexto agregado de las páginas del GIA.

    Usa la caché en disco si está vigente (24 h), salvo que `force_refresh`
    sea True. Si la red falla, devuelve marcadores de error en las páginas
    que no se pudieron obtener para no romper el arranque del servidor.
    """
    if not force_refresh:
        cache = _leer_cache()
        if cache:
            logger.info("Scraper: usando contexto cacheado.")
            return cache
    logger.info("Scraper: descargando contexto fresco del sitio del GIA.")
    contexto = _construir_contexto()
    _guardar_cache(contexto)
    return contexto


def refrescar_contexto_web() -> str:
    """Fuerza una recarga del contexto, ignorando la caché."""
    return obtener_contexto_web(force_refresh=True)


# Compatibilidad con el import existente en services/ai_service.py.
CONTEXTO_WEB = obtener_contexto_web()
