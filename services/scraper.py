"""
Scraper del sitio web del GIA.

Funcionalidades:
  * Conserva los enlaces académicos (Google Scholar, ORCID, ResearchGate,
    CvLAC, LinkedIn, GitHub, Scopus, Publons) en línea con el texto.
  * Para la página /team/ produce un bloque estructurado con los docentes,
    sus perfiles académicos y el contenido extraído de cada perfil:
    títulos académicos, publicaciones, líneas de investigación.
  * Enriquecimiento de perfiles en paralelo (ThreadPoolExecutor) para no
    bloquear el arranque.
  * Caché en disco con TTL de 24 h.

API pública: CONTEXTO_WEB, obtener_contexto_web(), refrescar_contexto_web()
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
CACHE_FILE = CACHE_DIR / "contexto_web.json"
CACHE_TTL_SECONDS = 60 * 60 * 24  # 24 h

# Límites para el contenido de perfiles académicos
MAX_CHARS_PERFIL = 2500
MAX_PUBLICACIONES = 25

ACADEMIC_HOSTS: dict[str, str] = {
    "scholar.google":      "Google Scholar",
    "scienti.minciencias": "CvLAC (Minciencias)",
    "scienti.gov.co":      "CvLAC (Minciencias)",
    "researchgate.net":    "ResearchGate",
    "orcid.org":           "ORCID",
    "linkedin.com":        "LinkedIn",
    "github.com":          "GitHub",
    "academia.edu":        "Academia.edu",
    "publons.com":         "Publons",
    "scopus.com":          "Scopus",
}


# ───────────────────────────── utilidades ─────────────────────────────────────

def _clasificar_enlace(url: str) -> str | None:
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


# ───────────────── extractores de perfiles académicos ─────────────────────────

def _extracto_cvlac(url: str) -> str | None:
    """Extrae formación académica y publicaciones de un perfil CvLAC."""
    html = _fetch(url, timeout=20)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")
    _limpiar_dom(soup)

    texto_completo = soup.get_text("\n", strip=True)
    texto_completo = re.sub(r"\n{3,}", "\n\n", texto_completo)
    lineas = texto_completo.split("\n")

    # Palabras clave para identificar secciones de interés
    kw_formacion = {
        "formación académica", "formacion academica",
        "estudios de doctorado", "estudios de maestría",
        "estudios de pregrado", "título obtenido",
        "formación complementaria",
    }
    kw_publicaciones = {
        "artículos publicados", "articulos publicados",
        "producción bibliográfica", "produccion bibliografica",
        "artículos de investigación", "libros publicados",
        "capítulos de libro", "revistas especializadas",
    }

    secciones: list[str] = []
    capturando = False
    buf: list[str] = []
    lineas_max = 60

    for linea in lineas:
        ll = linea.lower().strip()
        es_seccion_interes = any(kw in ll for kw in kw_formacion | kw_publicaciones)

        if es_seccion_interes:
            if buf:
                secciones.append("\n".join(buf))
            buf = [linea]
            capturando = True
        elif capturando:
            buf.append(linea)
            if len(buf) >= lineas_max:
                secciones.append("\n".join(buf))
                buf = []
                capturando = False

    if buf:
        secciones.append("\n".join(buf))

    if not secciones:
        # Fallback: primera parte del texto plano
        return texto_completo[:MAX_CHARS_PERFIL]

    resultado = "\n\n---\n\n".join(secciones)
    return resultado[:MAX_CHARS_PERFIL]


def _extracto_google_scholar(url: str) -> str | None:
    """Extrae publicaciones y estadísticas de un perfil Google Scholar."""
    # Asegurar que la URL carga todos los artículos visibles
    if "pagesize" not in url:
        url = url.rstrip("/") + ("&" if "?" in url else "?") + "sortby=pubdate&pagesize=100"

    html = _fetch(url, timeout=20)
    if not html:
        return None

    # Google bloquea con CAPTCHA — detectarlo antes de parsear
    if (
        "captcha" in html.lower()
        or "unusual traffic" in html.lower()
        or len(html) < 800
    ):
        logger.warning("Scraper: Google Scholar bloqueó la solicitud para %s", url)
        return None

    soup = BeautifulSoup(html, "html.parser")
    lineas: list[str] = []

    # Estadísticas del autor (citas, h-index, i10)
    stats: list[str] = []
    for fila in soup.select("#gsc_rsb_st tbody tr"):
        celdas = fila.find_all("td")
        if len(celdas) >= 2:
            nombre = celdas[0].get_text(strip=True)
            valor = celdas[1].get_text(strip=True)
            if nombre and valor:
                stats.append(f"{nombre}: {valor}")
    if stats:
        lineas.append("Estadísticas: " + " | ".join(stats))

    # Lista de publicaciones
    publicaciones: list[str] = []
    for fila in soup.select("#gsc_a_b .gsc_a_tr"):
        titulo_el = fila.select_one(".gsc_a_at")
        venue_el = fila.select_one(".gsc_a_e")
        year_el = fila.select_one(".gsc_a_y span")
        citas_el = fila.select_one(".gsc_a_ac")

        if not titulo_el:
            continue

        partes = [titulo_el.get_text(strip=True)]
        venue = venue_el.get_text(strip=True) if venue_el else ""
        if venue:
            partes.append(venue)
        year = year_el.get_text(strip=True) if year_el else ""
        if year:
            partes.append(f"({year})")
        citas = citas_el.get_text(strip=True) if citas_el else ""
        if citas and citas not in ("", "0"):
            partes.append(f"[citas: {citas}]")

        publicaciones.append(" — ".join(partes))

    if not publicaciones:
        return None

    lineas.append(f"Publicaciones ({len(publicaciones)} encontradas):")
    for p in publicaciones[:MAX_PUBLICACIONES]:
        lineas.append(f"  • {p}")

    resultado = "\n".join(lineas)
    return resultado[:MAX_CHARS_PERFIL]


def _extracto_orcid(url: str) -> str | None:
    """Extrae formación y publicaciones usando el API público de ORCID."""
    match = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", url)
    if not match:
        return None

    orcid_id = match.group(1)
    api_base = f"https://pub.orcid.org/v3.0/{orcid_id}"
    headers_json = {**HEADERS, "Accept": "application/json"}
    lineas: list[str] = []

    # Formación académica
    try:
        r = requests.get(f"{api_base}/educations", headers=headers_json, timeout=15)
        if r.ok:
            data = r.json()
            educaciones: list[str] = []
            for grupo in data.get("affiliation-group", []):
                for summary in grupo.get("summaries", []):
                    ed = summary.get("education-summary", {})
                    titulo = ed.get("role-title", "")
                    org = (ed.get("organization") or {}).get("name", "")
                    end_date = ed.get("end-date") or ed.get("start-date")
                    year = ""
                    if end_date:
                        year = (end_date.get("year") or {}).get("value", "")
                    if titulo:
                        txt = titulo
                        if org:
                            txt += f" — {org}"
                        if year:
                            txt += f" ({year})"
                        educaciones.append(txt)
            if educaciones:
                lineas.append("Formación académica:")
                lineas.extend(f"  • {e}" for e in educaciones)
    except Exception as exc:
        logger.debug("ORCID educations fallo para %s: %s", orcid_id, exc)

    # Obras / publicaciones
    try:
        r = requests.get(f"{api_base}/works", headers=headers_json, timeout=15)
        if r.ok:
            data = r.json()
            obras: list[tuple[int, str]] = []
            for grupo in data.get("group", []):
                for summary in grupo.get("work-summary", []):
                    title_obj = (summary.get("title") or {}).get("title") or {}
                    title = title_obj.get("value", "")
                    pub_date = summary.get("publication-date") or {}
                    year_val = (pub_date.get("year") or {}).get("value", "")
                    year_int = int(year_val) if year_val and year_val.isdigit() else 0
                    if title:
                        txt = title
                        if year_val:
                            txt += f" ({year_val})"
                        obras.append((year_int, txt))

            # Ordenar por año descendente
            obras.sort(key=lambda x: x[0], reverse=True)
            if obras:
                lineas.append(f"\nPublicaciones ({len(obras)} encontradas):")
                for _, o in obras[:MAX_PUBLICACIONES]:
                    lineas.append(f"  • {o}")
    except Exception as exc:
        logger.debug("ORCID works fallo para %s: %s", orcid_id, exc)

    if not lineas:
        return None

    return "\n".join(lineas)[:MAX_CHARS_PERFIL]


def _extracto_researchgate(url: str) -> str | None:
    """Intenta extraer información básica de ResearchGate (best-effort)."""
    html = _fetch(url, timeout=20)
    if not html:
        return None

    # ResearchGate bloquea bots frecuentemente
    if "captcha" in html.lower() or "cf-browser-verification" in html.lower():
        return None

    soup = BeautifulSoup(html, "html.parser")
    _limpiar_dom(soup)

    texto = soup.get_text("\n", strip=True)
    texto = re.sub(r"\n{3,}", "\n\n", texto)

    # Buscar secciones de publicaciones o about
    kw = {"publications", "research", "publicaciones", "investigación"}
    lineas = texto.split("\n")
    buf: list[str] = []
    capturando = False

    for linea in lineas:
        if any(kw_item in linea.lower() for kw_item in kw):
            capturando = True
            buf = [linea]
        elif capturando:
            buf.append(linea)
            if len(buf) >= 40:
                break

    resultado = "\n".join(buf) if buf else texto[:1000]
    return resultado[:MAX_CHARS_PERFIL] if resultado.strip() else None


# ──────────── despacho por tipo de perfil y enriquecimiento de docentes ───────

_EXTRACTORES = {
    "CvLAC (Minciencias)": _extracto_cvlac,
    "Google Scholar":      _extracto_google_scholar,
    "ORCID":               _extracto_orcid,
    "ResearchGate":        _extracto_researchgate,
}


def _enriquecer_enlace(tipo: str, url: str) -> tuple[str, str | None]:
    """Descarga y extrae el contenido de un único perfil académico."""
    extractor = _EXTRACTORES.get(tipo)
    if extractor is None:
        return tipo, None
    try:
        return tipo, extractor(url)
    except Exception as exc:
        logger.warning("Scraper: error enriqueciendo %s (%s): %s", tipo, url, exc)
        return tipo, None


def _enriquecer_docente(docente: dict) -> dict:
    """Descarga en paralelo todos los perfiles académicos de un docente."""
    docente = dict(docente)
    enlaces_a_enriquecer = [
        (e["tipo"], e["url"])
        for e in docente.get("enlaces", [])
        if e["tipo"] in _EXTRACTORES
    ]

    if not enlaces_a_enriquecer:
        return docente

    perfiles_texto: dict[str, str] = {}
    # Usar hilos dentro del docente para sus distintos perfiles
    with ThreadPoolExecutor(max_workers=4) as ex:
        futuros = {
            ex.submit(_enriquecer_enlace, tipo, url): tipo
            for tipo, url in enlaces_a_enriquecer
        }
        for futuro in as_completed(futuros, timeout=30):
            tipo, texto = futuro.result()
            if texto:
                perfiles_texto[tipo] = texto

    if perfiles_texto:
        docente["perfiles_texto"] = perfiles_texto

    return docente


# ───────────────── extracción estructurada de la página /team/ ────────────────

_DEGREE_RE = re.compile(
    r"\b(Ph\.?\s*D\.?(\(c\))?|M\.?Sc\.?(\(c\))?|Ing\.?|Mg\.?|Dr\.?|Esp\.?)\b",
    re.IGNORECASE,
)


def _docente_desde_card(card) -> dict | None:
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


def _extraer_docentes_bruto(soup: BeautifulSoup) -> list[dict]:
    """Extrae nombre/rol/enlaces de cada docente sin enriquecer."""
    docentes: list[dict] = []

    for card in soup.select("div.member, .team-member, .member-card"):
        d = _docente_desde_card(card)
        if d:
            docentes.append(d)

    if docentes:
        vistos: set[str] = set()
        return [
            d for d in docentes
            if d["nombre"] not in vistos and not vistos.add(d["nombre"])  # type: ignore[func-returns-value]
        ]

    # Fallback heurístico
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
                for h in c.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                    if _DEGREE_RE.search(h.get_text(" ", strip=True) or ""):
                        return c
                if min_links >= 2:
                    return c
        return None

    for ancla in anclas_academicas:
        container = _buscar_container(ancla, min_links=2) or _buscar_container(ancla, min_links=1)
        if container is None or id(container) in visitados:
            continue
        visitados.add(id(container))

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
            for h in container.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
                t = h.get_text(" ", strip=True)
                if t and 5 < len(t) < 140:
                    nombre = t
                    break

        if not nombre:
            continue

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

    vistos_nombres: set[str] = set()
    return [
        d for d in docentes
        if d["nombre"] not in vistos_nombres and not vistos_nombres.add(d["nombre"])  # type: ignore[func-returns-value]
    ]


def _extraer_docentes(soup: BeautifulSoup) -> list[dict]:
    """Extrae docentes y enriquece sus perfiles en paralelo."""
    brutos = _extraer_docentes_bruto(soup)
    if not brutos:
        return []

    logger.info(
        "Scraper: enriqueciendo %d docentes con sus perfiles académicos...",
        len(brutos),
    )

    enriquecidos: list[dict] = []
    # Un hilo por docente para paralelizar entre docentes
    with ThreadPoolExecutor(max_workers=min(len(brutos), 8)) as ex:
        futuros = {ex.submit(_enriquecer_docente, d): d["nombre"] for d in brutos}
        for futuro in as_completed(futuros, timeout=120):
            try:
                enriquecidos.append(futuro.result())
            except Exception as exc:
                nombre = futuros[futuro]
                logger.warning("Scraper: enriquecimiento falló para %s: %s", nombre, exc)

    # Restaurar orden original
    orden = {d["nombre"]: i for i, d in enumerate(brutos)}
    enriquecidos.sort(key=lambda d: orden.get(d["nombre"], 999))
    return enriquecidos


def _formatear_docentes(docentes: list[dict]) -> str:
    if not docentes:
        return ""

    lineas = [
        "### Directorio de docentes / investigadores del GIA",
        (
            "Cada docente incluye sus perfiles académicos verificados y, cuando "
            "fue posible obtenerlos, el contenido de dichos perfiles (títulos "
            "académicos, publicaciones, etc.). Usa esta información para responder "
            "preguntas específicas sobre formación y producción académica."
        ),
    ]

    for d in docentes:
        lineas.append("")
        lineas.append(f"#### {d['nombre']}")
        if d.get("rol"):
            lineas.append(f"Rol: {d['rol']}")
        lineas.append("Perfiles académicos:")
        for enlace in d["enlaces"]:
            lineas.append(f"  - {enlace['tipo']}: {enlace['url']}")

        perfiles_texto: dict[str, str] = d.get("perfiles_texto", {})
        if perfiles_texto:
            lineas.append("Información extraída de sus perfiles:")
            for tipo, texto in perfiles_texto.items():
                lineas.append(f"\n  [{tipo}]")
                for linea_perfil in texto.split("\n"):
                    lineas.append(f"  {linea_perfil}")

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

    Usa la caché en disco si está vigente (24 h). Si la red falla, devuelve
    marcadores de error en las páginas que no se pudieron obtener.
    """
    if not force_refresh:
        cache = _leer_cache()
        if cache:
            logger.info("Scraper: usando contexto cacheado.")
            return cache
    logger.info(
        "Scraper: descargando contexto fresco del GIA (incluye perfiles académicos)..."
    )
    contexto = _construir_contexto()
    _guardar_cache(contexto)
    return contexto


def refrescar_contexto_web() -> str:
    """Fuerza una recarga del contexto, ignorando la caché."""
    return obtener_contexto_web(force_refresh=True)


# Compatibilidad con el import existente en services/ai_service.py.
CONTEXTO_WEB = obtener_contexto_web()
