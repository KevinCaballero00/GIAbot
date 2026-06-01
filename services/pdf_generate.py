import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "generados"

_COLOR_PRINCIPAL = colors.HexColor("#1a3a5c")
_COLOR_GRIS = colors.HexColor("#666666")
_COLOR_LINEA = colors.HexColor("#cccccc")


def _escape(value) -> str:
    """Escapa caracteres especiales XML para uso en ReportLab Paragraph."""
    if value is None:
        return "N/D"
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _val(value) -> str:
    """Devuelve el valor como str o 'N/D' si es nulo o vacío."""
    if value is None or str(value).strip() == "":
        return "N/D"
    return str(value).strip()


def _para_texto(value) -> str:
    """Escapa texto y convierte saltos de línea a <br/> para Paragraph."""
    if value is None or str(value).strip() == "":
        return "N/D"
    safe = _escape(str(value))
    safe = re.sub(r"\r\n|\r", "\n", safe)
    safe = re.sub(r"\n{3,}", "\n\n", safe)
    return safe.replace("\n", "<br/>")


def _link(url: str, display: str = "Ver fuente") -> str:
    """Genera etiqueta <link> de ReportLab con URL y texto de display escapados."""
    return f'<link href="{_escape(url)}" color="blue">{_escape(display)}</link>'


def _build_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle(
            "Titulo",
            parent=base["Title"],
            fontSize=13,
            spaceAfter=4,
            textColor=_COLOR_PRINCIPAL,
        ),
        "subtitulo": ParagraphStyle(
            "Subtitulo",
            parent=base["Normal"],
            fontSize=10,
            spaceAfter=2,
            textColor=_COLOR_GRIS,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontSize=11,
            spaceBefore=10,
            spaceAfter=4,
            textColor=_COLOR_PRINCIPAL,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontSize=10,
            spaceBefore=6,
            spaceAfter=2,
            textColor=_COLOR_PRINCIPAL,
        ),
        "normal": ParagraphStyle(
            "Normal2",
            parent=base["Normal"],
            fontSize=10,
            leading=14,
            spaceAfter=3,
        ),
        "meta": ParagraphStyle(
            "Meta",
            parent=base["Normal"],
            fontSize=9,
            textColor=_COLOR_GRIS,
            leading=12,
        ),
        "desc": ParagraphStyle(
            "Desc",
            parent=base["Normal"],
            fontSize=9,
            leading=13,
            leftIndent=12,
            spaceAfter=4,
        ),
    }


def generar_pdf_fo_in_13(resultado: dict) -> str:
    """
    Genera el PDF del FO-IN-13 con formato fiel al documento oficial.
    Delega al nuevo template; mantiene este nombre para compatibilidad con routes/chat.py.
    Retorna la ruta absoluta del archivo generado.
    """
    from services.pdf_fo_in_13 import generar_pdf_fo_in_13_plantilla
    return generar_pdf_fo_in_13_plantilla(resultado)


def _generar_pdf_fo_in_13_legacy(resultado: dict) -> str:
    """Implementación anterior (respaldo)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docente_info = resultado.get("docente") or {}
    nombre_docente = _val(docente_info.get("nombre"))
    nombre_slug = re.sub(r"[^\w]", "_", nombre_docente)[:25].strip("_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"FO-IN-13_{nombre_slug}_{ts}.pdf"
    ruta = OUTPUT_DIR / nombre_archivo

    doc = SimpleDocTemplate(
        str(ruta),
        pagesize=letter,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )

    s = _build_styles()
    content: list = []

    # ── Encabezado ────────────────────────────────────────────────────────────
    content.append(Paragraph(
        "FO-IN-13 – Informe de Gestión de Grupos de Investigación",
        s["titulo"],
    ))
    content.append(Paragraph(
        "Grupo GIA – Universidad Francisco de Paula Santander",
        s["subtitulo"],
    ))
    content.append(HRFlowable(width="100%", thickness=1, color=_COLOR_LINEA))
    content.append(Spacer(1, 0.3 * cm))

    # ── Datos del informe ─────────────────────────────────────────────────────
    periodo = _val(resultado.get("periodo"))
    fecha_ext = resultado.get("fecha_extraccion", "")
    fecha_formato = fecha_ext[:10] if fecha_ext else "N/D"

    tabla_datos = [
        ["Docente:", nombre_docente],
        ["Período:", periodo],
        ["Fecha de extracción:", fecha_formato],
    ]
    t = Table(tabla_datos, colWidths=[4.5 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    content.append(t)
    content.append(Spacer(1, 0.5 * cm))

    # ── Proyectos ─────────────────────────────────────────────────────────────
    proyectos = resultado.get("proyectos", [])
    proyectos_validos = [
        p for p in proyectos
        if p.get("proyecto") and not p.get("error")
    ]

    content.append(Paragraph("Proyectos Identificados", s["h2"]))
    content.append(Paragraph(
        f"Total de entradas: <b>{len(proyectos_validos)}</b>",
        s["meta"],
    ))
    content.append(Spacer(1, 0.2 * cm))

    if proyectos_validos:
        for i, p in enumerate(proyectos_validos, 1):
            titulo_p = _val(p.get("proyecto"))
            bloque: list = []

            bloque.append(Paragraph(
                f"<b>{i}. {_escape(titulo_p)}</b>",
                s["h3"],
            ))

            meta_partes: list[str] = []
            if p.get("fuente"):
                meta_partes.append(f"Fuente: {_escape(_val(p['fuente']))}")
            if p.get("docente"):
                meta_partes.append(f"Docente: {_escape(_val(p['docente']))}")
            if p.get("periodo"):
                meta_partes.append(f"Período: {_escape(_val(p['periodo']))}")
            if meta_partes:
                bloque.append(Paragraph(
                    "   " + " | ".join(meta_partes),
                    s["meta"],
                ))

            desc = (p.get("descripcion") or "").strip()
            if desc:
                bloque.append(Paragraph(_para_texto(desc), s["desc"]))

            url = p.get("enlace_origen")
            if url:
                bloque.append(Paragraph(
                    "   " + _link(url, "Ver fuente original"),
                    s["meta"],
                ))

            bloque.append(Spacer(1, 0.3 * cm))
            content.append(KeepTogether(bloque))
    else:
        content.append(Paragraph(
            "No se encontraron proyectos en las fuentes consultadas para este período.",
            s["normal"],
        ))

    # ── Fuentes consultadas ───────────────────────────────────────────────────
    fuentes = resultado.get("fuentes_consultadas", [])
    if fuentes:
        content.append(Paragraph("Fuentes Consultadas", s["h2"]))
        for f in fuentes:
            content.append(Paragraph(f"• {_escape(_val(f))}", s["normal"]))

    # ── Errores / advertencias ────────────────────────────────────────────────
    errores = resultado.get("errores", [])
    if errores:
        content.append(Spacer(1, 0.3 * cm))
        content.append(Paragraph("Advertencias", s["h2"]))
        for e in errores:
            content.append(Paragraph(f"* {_escape(_val(e))}", s["meta"]))

    # ── Pie ───────────────────────────────────────────────────────────────────
    content.append(Spacer(1, 0.5 * cm))
    content.append(HRFlowable(width="100%", thickness=0.5, color=_COLOR_LINEA))
    content.append(Paragraph(
        f"Documento generado automáticamente por GIAbot el {_escape(fecha_formato)}.",
        s["meta"],
    ))

    doc.build(content)
    return str(ruta)
