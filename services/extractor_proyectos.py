"""
Extractor de proyectos para los informes FO-IN-13 y FO-IN-17.

Extrae información de proyectos del GIA desde:
  - gia.ufps.edu.co/proyectos/
  - Perfiles CvLAC de cada docente listado en /team/

Normaliza los datos a JSON con campos: fuente, docente, proyecto,
periodo, descripcion, enlace_origen, fecha_extraccion.
Guarda el resultado en static/extracciones/ y retorna la ruta.

Fallos por fuente son aislados: si un CvLAC no responde, ese docente
queda marcado con error y el proceso continúa con las demás fuentes.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from services.scraper import (
    BASE_URL,
    _fetch,
    _limpiar_dom,
    _extraer_docentes_bruto,
)

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "extracciones"

KW_PROYECTOS = {
    "proyectos de investigación",
    "proyectos de investigacion",
    "proyectos de extensión",
    "proyectos de extension",
    "proyectos de formación",
    "proyectos de formacion",
    "proyecto de investigación",
    "proyecto de investigacion",
    "proyectos en curso",
    "proyectos vigentes",
    "proyecto:",
}

PERIODO_RE = re.compile(r"\b(20\d{2}[-–/]\d{1,2}|20\d{2})\b")
MAX_LINEAS_SECCION = 60


# ─────────────────────────── extractor CvLAC ─────────────────────────────────

def _extraer_proyectos_cvlac(nombre_docente: str, cvlac_url: str) -> list[dict]:
    """Extrae secciones de proyectos del perfil CvLAC de un docente."""
    fecha = datetime.utcnow().isoformat()
    html = _fetch(cvlac_url, timeout=20)

    if not html:
        return [{
            "fuente": "CvLAC (Minciencias)",
            "docente": nombre_docente,
            "proyecto": None,
            "periodo": None,
            "descripcion": None,
            "enlace_origen": cvlac_url,
            "fecha_extraccion": fecha,
            "error": "No se pudo obtener el perfil CvLAC",
        }]

    soup = BeautifulSoup(html, "html.parser")
    _limpiar_dom(soup)
    texto = soup.get_text("\n", strip=True)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    lineas = texto.split("\n")

    secciones: list[list[str]] = []
    buf: list[str] = []
    capturando = False

    for linea in lineas:
        ll = linea.lower().strip()
        es_inicio = any(kw in ll for kw in KW_PROYECTOS)

        if es_inicio:
            if buf:
                secciones.append(buf)
            buf = [linea]
            capturando = True
        elif capturando:
            if not ll and len(buf) > 2:
                secciones.append(buf)
                buf = []
                capturando = False
            else:
                buf.append(linea)
                if len(buf) >= MAX_LINEAS_SECCION:
                    secciones.append(buf)
                    buf = []
                    capturando = False

    if buf:
        secciones.append(buf)

    if not secciones:
        return [{
            "fuente": "CvLAC (Minciencias)",
            "docente": nombre_docente,
            "proyecto": None,
            "periodo": None,
            "descripcion": "No se encontraron secciones de proyectos en el perfil",
            "enlace_origen": cvlac_url,
            "fecha_extraccion": fecha,
        }]

    resultado: list[dict] = []
    for sec in secciones:
        texto_sec = "\n".join(sec).strip()
        titulo = sec[0].strip()

        periodo = None
        for linea in sec:
            m = PERIODO_RE.search(linea)
            if m:
                periodo = m.group(0)
                break

        resultado.append({
            "fuente": "CvLAC (Minciencias)",
            "docente": nombre_docente,
            "proyecto": titulo,
            "periodo": periodo,
            "descripcion": texto_sec[:1200],
            "enlace_origen": cvlac_url,
            "fecha_extraccion": fecha,
        })

    return resultado


# ─────────────────────── extractor página GIA /proyectos/ ────────────────────

def _extraer_proyectos_gia() -> list[dict]:
    """Extrae proyectos listados en gia.ufps.edu.co/proyectos/."""
    url = f"{BASE_URL}/proyectos/"
    fecha = datetime.utcnow().isoformat()
    html = _fetch(url, timeout=20)

    if not html:
        return [{
            "fuente": "GIA Web – /proyectos/",
            "docente": None,
            "proyecto": None,
            "periodo": None,
            "descripcion": None,
            "enlace_origen": url,
            "fecha_extraccion": fecha,
            "error": "No se pudo obtener la página de proyectos",
        }]

    soup = BeautifulSoup(html, "html.parser")
    _limpiar_dom(soup)
    proyectos: list[dict] = []

    # Intento estructurado: cards / articles con título
    for card in soup.select("article, .proyecto, .project, .card, .entry, section"):
        titulo_el = card.find(["h1", "h2", "h3", "h4"])
        if not titulo_el:
            continue
        titulo = titulo_el.get_text(" ", strip=True)
        if not titulo or len(titulo) < 5:
            continue

        desc = " ".join(p.get_text(" ", strip=True) for p in card.find_all("p"))
        periodo = None
        m = PERIODO_RE.search(card.get_text(" ", strip=True))
        if m:
            periodo = m.group(0)

        proyectos.append({
            "fuente": "GIA Web – /proyectos/",
            "docente": None,
            "proyecto": titulo,
            "periodo": periodo,
            "descripcion": desc[:800] if desc else None,
            "enlace_origen": url,
            "fecha_extraccion": fecha,
        })

    if not proyectos:
        # Fallback: encabezados h2/h3/h4 con el párrafo siguiente
        for h in soup.find_all(["h2", "h3", "h4"]):
            titulo = h.get_text(" ", strip=True)
            if not titulo or len(titulo) < 8:
                continue
            siguiente = h.find_next_sibling(["p", "div"])
            desc = siguiente.get_text(" ", strip=True) if siguiente else None
            proyectos.append({
                "fuente": "GIA Web – /proyectos/",
                "docente": None,
                "proyecto": titulo,
                "periodo": None,
                "descripcion": desc[:600] if desc else None,
                "enlace_origen": url,
                "fecha_extraccion": fecha,
            })

    if not proyectos:
        # Último recurso: texto plano de la página completa
        texto = soup.get_text("\n", strip=True)
        proyectos.append({
            "fuente": "GIA Web – /proyectos/",
            "docente": None,
            "proyecto": None,
            "periodo": None,
            "descripcion": texto[:2000],
            "enlace_origen": url,
            "fecha_extraccion": fecha,
            "nota": "Sin estructura detectada; se incluye texto plano de la página",
        })

    return proyectos


# ─────────────────────── obtener docentes con CvLAC ──────────────────────────

def _obtener_docentes_cvlac() -> list[tuple[str, str]]:
    """Devuelve lista de (nombre_docente, cvlac_url) desde /team/."""
    url = f"{BASE_URL}/team/"
    html = _fetch(url, timeout=20)
    if not html:
        logger.warning("Extractor: no se pudo obtener /team/ para listar CvLAC")
        return []

    soup = BeautifulSoup(html, "html.parser")
    _limpiar_dom(soup)
    docentes = _extraer_docentes_bruto(soup)

    resultado: list[tuple[str, str]] = []
    for d in docentes:
        for enlace in d.get("enlaces", []):
            if enlace["tipo"] == "CvLAC (Minciencias)":
                resultado.append((d["nombre"], enlace["url"]))
                break

    return resultado


# ──────────────────────────── utilidades de período ──────────────────────────

def calcular_periodo() -> tuple[str, int, int]:
    """Calcula el período académico actual. Retorna (periodo_str, semestre, anio)."""
    hoy = datetime.now()
    semestre = 1 if 2 <= hoy.month <= 7 else 2
    return f"{hoy.year}-{semestre}", semestre, hoy.year


def _normalizar_nombre(nombre: str) -> str:
    nfkd = unicodedata.normalize("NFKD", nombre.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _nombre_coincide(nombre_db: str, nombre_web: str) -> bool:
    palabras_db = set(_normalizar_nombre(nombre_db).split())
    palabras_web = set(_normalizar_nombre(nombre_web).split())
    return len(palabras_db & palabras_web) >= 2


# ────────────────────────────── orquestador ──────────────────────────────────

def extraer_proyectos(docente: dict | None = None) -> dict:
    """
    Extrae proyectos de todas las fuentes, guarda el resultado en JSON y lo retorna.

    Si se proporciona `docente`, solo se consulta el CvLAC que coincida con ese nombre.
    El dict retornado incluye:
      - docente: datos del docente autenticado (o None)
      - periodo: período académico calculado (ej. "2026-1")
      - proyectos: lista de entradas normalizadas
      - fuentes_consultadas: lista de fuentes intentadas
      - errores: mensajes de error por fuente
      - fecha_extraccion: timestamp ISO de inicio
      - duracion_segundos: tiempo total de extracción
      - _ruta_archivo: ruta absoluta del JSON generado
      - _nombre_archivo: nombre del archivo para construir el enlace
    """
    logger.info("Extractor: iniciando extracción de proyectos...")
    inicio = datetime.utcnow()
    todas: list[dict] = []
    errores: list[str] = []
    fuentes_consultadas: list[str] = []

    periodo, _, _ = calcular_periodo()

    # 1. Página institucional /proyectos/
    fuentes_consultadas.append("GIA Web – /proyectos/")
    try:
        entradas_gia = _extraer_proyectos_gia()
        todas.extend(entradas_gia)
        logger.info("Extractor: %d entradas desde GIA /proyectos/", len(entradas_gia))
    except Exception as exc:
        msg = f"GIA Web /proyectos/: {exc}"
        logger.warning("Extractor: fallo – %s", msg)
        errores.append(msg)

    # 2. Perfiles CvLAC (filtrados al docente si se proporcionó)
    todos_cvlac = _obtener_docentes_cvlac()
    if docente:
        docentes_cvlac = [
            (n, u) for n, u in todos_cvlac
            if _nombre_coincide(docente["nombre"], n)
        ]
        logger.info(
            "Extractor: filtrando CvLAC para '%s' – %d coincidencias",
            docente["nombre"], len(docentes_cvlac),
        )
    else:
        docentes_cvlac = todos_cvlac

    logger.info("Extractor: %d docentes con CvLAC a consultar", len(docentes_cvlac))

    if docentes_cvlac:
        for nombre_doc, _ in docentes_cvlac:
            fuentes_consultadas.append(f"CvLAC de {nombre_doc}")

        with ThreadPoolExecutor(max_workers=min(len(docentes_cvlac), 6)) as ex:
            futuros = {
                ex.submit(_extraer_proyectos_cvlac, nombre, url): nombre
                for nombre, url in docentes_cvlac
            }
            for futuro in as_completed(futuros, timeout=90):
                nombre = futuros[futuro]
                try:
                    entradas = futuro.result()
                    todas.extend(entradas)
                    logger.info(
                        "Extractor: %d entradas desde CvLAC de %s",
                        len(entradas), nombre,
                    )
                except Exception as exc:
                    msg = f"CvLAC de {nombre}: {exc}"
                    logger.warning("Extractor: fallo – %s", msg)
                    errores.append(msg)

    fin = datetime.utcnow()
    resultado = {
        "docente": docente,
        "periodo": periodo,
        "proyectos": todas,
        "fuentes_consultadas": fuentes_consultadas,
        "errores": errores,
        "fecha_extraccion": inicio.isoformat(),
        "duracion_segundos": round((fin - inicio).total_seconds(), 2),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = inicio.strftime("%Y%m%d_%H%M%S")
    usuario_slug = docente["usuario"] if docente else "todos"
    nombre_archivo = f"proyectos_{usuario_slug}_{ts}.json"
    ruta = OUTPUT_DIR / nombre_archivo
    ruta.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Extractor: resultado guardado en %s", ruta)

    resultado["_ruta_archivo"] = str(ruta)
    resultado["_nombre_archivo"] = nombre_archivo
    return resultado
