"""
Utilidad de dev-time: extrae la firma del director embebida en el FO-IN-17
oficial y la guarda como PNG estable para incrustarla en los PDFs generados.

La firma vive en la página 8 del PDF original como imagen embebida RGBA
(`Image39.png`, ~397x131). La otra imagen de esa página es el logo UFPS
(`Image11.png`, RGB cuadrado), que se descarta.

Es idempotente: si el PNG destino ya existe, no hace nada.

Uso:
    python -m services.extraer_firma        (desde GIAbot/)
    python services/extraer_firma.py
"""
from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

logger = logging.getLogger(__name__)

_DOCS_DIR = Path(__file__).resolve().parent.parent / "static" / "docs"
PDF_ORIGINAL = _DOCS_DIR / "FO-IN-17 PLAN DE ACCION GRUPOS INV V1.pdf"
FIRMA_PATH = _DOCS_DIR / "firma_director.png"

# Página (0-indexada) donde está el bloque de firmas del FO-IN-17 oficial.
_PAGINA_FIRMA = 7


def _elegir_firma(imagenes) -> object | None:
    """
    Elige la imagen de la firma entre las embebidas en la página.

    Heurística: la firma es ancha (aspect ratio > 1.8) y, normalmente, RGBA
    (tiene transparencia). El logo UFPS es aproximadamente cuadrado y RGB.
    Como respaldo, si nada cumple la heurística se toma la imagen más ancha.
    """
    candidatos = []
    for img in imagenes:
        try:
            w, h = img.image.size
        except Exception:
            continue
        if not h:
            continue
        aspecto = w / h
        es_rgba = getattr(img.image, "mode", "") == "RGBA"
        candidatos.append((aspecto, es_rgba, img))

    if not candidatos:
        return None

    # Preferir anchas con transparencia; luego anchas; luego la más ancha.
    firmas = [c for c in candidatos if c[0] > 1.8 and c[1]]
    if not firmas:
        firmas = [c for c in candidatos if c[0] > 1.8]
    if not firmas:
        firmas = sorted(candidatos, key=lambda c: c[0], reverse=True)[:1]

    return firmas[0][2]


def extraer_firma_director(forzar: bool = False) -> Path | None:
    """
    Extrae la firma del director del FO-IN-17 oficial a `firma_director.png`.

    Retorna la ruta del PNG si está disponible (recién creado o ya existente),
    o None si no se pudo extraer.
    """
    if FIRMA_PATH.exists() and not forzar:
        logger.info("Firma ya existe en %s; no se regenera.", FIRMA_PATH)
        return FIRMA_PATH

    if not PDF_ORIGINAL.exists():
        logger.error("No se encontró el PDF original: %s", PDF_ORIGINAL)
        return None

    try:
        reader = PdfReader(str(PDF_ORIGINAL))
        pagina = reader.pages[_PAGINA_FIRMA]
        firma = _elegir_firma(pagina.images)
    except Exception as exc:
        logger.error("Error al leer el PDF original para extraer la firma: %s", exc)
        return None

    if firma is None:
        logger.error("No se identificó ninguna imagen de firma en la página %d.", _PAGINA_FIRMA + 1)
        return None

    try:
        FIRMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        firma.image.save(str(FIRMA_PATH))
        logger.info("Firma extraída a %s (%s)", FIRMA_PATH, firma.image.size)
        return FIRMA_PATH
    except Exception as exc:
        logger.error("Error al guardar la firma extraída: %s", exc)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ruta = extraer_firma_director(forzar=True)
    if ruta:
        print(f"OK: firma disponible en {ruta}")
    else:
        print("ERROR: no se pudo extraer la firma.")
        raise SystemExit(1)
