"""
Validadores/normalizadores compartidos para campos de las secciones 2/3/4 del
FO-IN-17 (trabajos de grado dirigidos, eventos): nivel académico y fechas.

Se usan tanto desde el flujo conversacional (`routes/chat.py`) como desde el
estructurador de documentos subidos (`services/estructurador.py`), para que
ambos caminos apliquen exactamente las mismas reglas de normalización.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

_NIVELES_VALIDOS = {
    "pregrado": "Pregrado",
    "especializacion": "Especialización",
    "especializaciones": "Especialización",
    "maestria": "Maestría",
    "doctorado": "Doctorado",
}

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}


def quitar_tildes(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def parse_nivel(texto: str) -> str | None:
    """Normaliza el nivel de un trabajo de grado a uno de los 4 valores oficiales."""
    t = quitar_tildes((texto or "").strip().lower())
    for k, v in _NIVELES_VALIDOS.items():
        if k in t:
            return v
    return None


def parse_fecha(texto: str) -> str | None:
    """
    Normaliza una fecha a DD/MM/AAAA. Acepta DD/MM/AAAA, DD-MM-AAAA, AAAA-MM-DD
    y "DD de <mes> de AAAA". Retorna None si no reconoce ningún formato de
    fecha (el llamador decide si reintentar o aceptar el texto literal).
    """
    t = (texto or "").strip()

    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%d/%m/%Y")
        except ValueError:
            return None

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%d/%m/%Y")
        except ValueError:
            return None

    m = re.match(r"^(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})$", quitar_tildes(t.lower()))
    if m:
        d = int(m.group(1))
        mo = _MESES_ES.get(m.group(2))
        y = int(m.group(3))
        if mo:
            try:
                return datetime(y, mo, d).strftime("%d/%m/%Y")
            except ValueError:
                return None

    return None
