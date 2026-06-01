"""
Generador de PDF con formato fiel al FO-IN-17 oficial.
Usa el JSON de extraer_proyectos() como fuente de datos.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "generados"

_ROJO = colors.HexColor("#C00000")
_NARANJA = colors.HexColor("#F4B942")
_GRIS_CLARO = colors.HexColor("#F2F2F2")
_NEGRO = colors.black
_BLANCO = colors.white

_BASE = getSampleStyleSheet()

_NORMAL = ParagraphStyle(
    "FO17Normal", parent=_BASE["Normal"],
    fontName="Helvetica", fontSize=8, leading=10, spaceAfter=0,
)
_BOLD = ParagraphStyle("FO17Bold", parent=_NORMAL, fontName="Helvetica-Bold")
_HEADER_BLANCO = ParagraphStyle(
    "FO17HdrBlanco", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=9, textColor=_BLANCO, alignment=1,
)
_HEADER_NEGRO = ParagraphStyle(
    "FO17HdrNegro", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=8, textColor=_NEGRO, alignment=1,
)
_LABEL = ParagraphStyle("FO17Label", parent=_NORMAL, fontName="Helvetica-Bold", fontSize=8)
_VALOR = ParagraphStyle("FO17Valor", parent=_NORMAL, fontName="Helvetica", fontSize=8)
_NOTA = ParagraphStyle(
    "FO17Nota", parent=_NORMAL,
    fontName="Helvetica-Oblique", fontSize=7.5, leading=10,
)
_INTRO = ParagraphStyle("FO17Intro", parent=_NORMAL, fontName="Helvetica", fontSize=8, leading=11)


def _e(v) -> str:
    if v is None:
        return ""
    return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _v(v) -> str:
    if v is None or str(v).strip() == "":
        return ""
    return str(v).strip()


def _p(txt: str, style=None) -> Paragraph:
    return Paragraph(_e(txt), style or _NORMAL)


def _periodo_a_semestre_anio(periodo: str) -> tuple[str, str]:
    m = re.match(r"(\d{4})[-–/]([12])", periodo or "")
    if m:
        return m.group(2), m.group(1)
    return "1", str(datetime.now().year)


def _split_actividades(desc: str | None, max_chars: int = 150, max_items: int = 5) -> list[str]:
    """Divide descripción en actividades cortas. Limita chars y cantidad para que la
    celda nunca supere la altura del frame de página (~670pt)."""
    if not desc or not desc.strip():
        return [""]
    texto = re.sub(r"\r\n|\r", "\n", desc.strip())
    lineas_brutas = [ln.strip() for ln in texto.split("\n") if ln.strip()]

    resultado: list[str] = []
    for linea in lineas_brutas:
        if len(linea) > max_chars:
            linea = linea[:max_chars].rsplit(" ", 1)[0] + "..."
        resultado.append(linea)

    return resultado[:max_items] if resultado else [""]


# ── Canvas con encabezado ─────────────────────────────────────────────────────

class _EncabezadoCanvas(rl_canvas.Canvas):
    def __init__(self, *args, titulo_doc: str = "", codigo: str = "", fecha_doc: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict] = []
        self._titulo_doc = titulo_doc
        self._codigo = codigo
        self._fecha_doc = fecha_doc

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_page_states)
        for i, state in enumerate(self._saved_page_states, 1):
            self.__dict__.update(state)
            self._dibujar_encabezado(i, total)
            self._dibujar_pie()
            rl_canvas.Canvas.showPage(self)
        rl_canvas.Canvas.save(self)

    def _dibujar_encabezado(self, pagina: int, total: int):
        W, H = letter
        self.saveState()

        top = H - 1.0 * cm
        h_hdr = 1.8 * cm
        h_elab = 0.6 * cm
        col1 = 3.2 * cm
        col_mid = 9.0 * cm
        col_right_lbl = 1.6 * cm
        col_right_val = 2.0 * cm
        x0 = 1.5 * cm

        self.setStrokeColor(_NEGRO)
        self.setLineWidth(0.5)

        self.rect(x0, top - h_hdr, col1, h_hdr)
        self.setFont("Helvetica-Bold", 7)
        self.setFillColor(_NEGRO)
        self.drawCentredString(x0 + col1 / 2, top - 0.5 * cm, "UF")
        self.drawCentredString(x0 + col1 / 2, top - 0.9 * cm, "PS")
        self.setFont("Helvetica", 5.5)
        self.drawCentredString(x0 + col1 / 2, top - 1.2 * cm, "Universidad Francisco")
        self.drawCentredString(x0 + col1 / 2, top - 1.45 * cm, "de Paula Santander")

        x_mid = x0 + col1
        self.rect(x_mid, top - h_hdr, col_mid, h_hdr)
        self.setFont("Helvetica-Bold", 10)
        self.setFillColor(_NEGRO)
        self.drawCentredString(x_mid + col_mid / 2, top - 0.55 * cm, "INVESTIGACIÓN")
        self.setFillColor(_ROJO)
        self.rect(x_mid, top - h_hdr, col_mid, 0.8 * cm, fill=1, stroke=0)
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(_BLANCO)
        self.drawCentredString(x_mid + col_mid / 2, top - h_hdr + 0.25 * cm, self._titulo_doc)

        x_right = x_mid + col_mid
        fila_h = h_hdr / 4
        filas = [
            ("CÓDIGO", self._codigo),
            ("VERSIÓN", "01"),
            ("FECHA", self._fecha_doc),
            ("PÁGINA", f"{pagina} de {total}"),
        ]
        for j, (lbl, val) in enumerate(filas):
            y_f = top - (j + 1) * fila_h
            self.rect(x_right, y_f, col_right_lbl + col_right_val, fila_h, fill=0, stroke=1)
            self.setFont("Helvetica-Bold", 6.5)
            self.setFillColor(_NEGRO)
            self.drawString(x_right + 0.1 * cm, y_f + 0.1 * cm, lbl)
            self.setFont("Helvetica", 6.5)
            self.drawString(x_right + col_right_lbl + 0.05 * cm, y_f + 0.1 * cm, val)
            self.line(x_right + col_right_lbl, y_f, x_right + col_right_lbl, y_f + fila_h)

        y_elab = top - h_hdr - h_elab
        W_total = W - 3.0 * cm
        col_e = W_total / 3
        etiquetas = ["ELABORÓ", "REVISÓ", "APROBÓ"]
        valores = ["Líder Investigación", "Equipo Operativo de Calidad", "Líder de Calidad"]
        for j in range(3):
            xj = x0 + j * col_e
            self.rect(xj, y_elab, col_e, h_elab, fill=0, stroke=1)
            self.setFont("Helvetica-Bold", 7)
            self.setFillColor(_NEGRO)
            self.drawCentredString(xj + col_e / 2, y_elab + h_elab - 0.22 * cm, etiquetas[j])
            self.setFont("Helvetica", 6.5)
            self.drawCentredString(xj + col_e / 2, y_elab + 0.08 * cm, valores[j])

        self.restoreState()

    def _dibujar_pie(self):
        W, _ = letter
        self.saveState()
        self.setFont("Helvetica-Bold", 7)
        self.setFillColor(_NEGRO)
        self.drawCentredString(W / 2, 0.8 * cm, "Documento Digital - Copia controlada")
        self.setFont("Helvetica-Oblique", 6.5)
        self.drawCentredString(W / 2, 0.55 * cm,
            "(La descarga o impresión de este documento le da carácter de COPIA NO CONTROLADA)")
        self.restoreState()


# ── Helpers ───────────────────────────────────────────────────────────────────

_BORDE_BASE = [
    ("GRID", (0, 0), (-1, -1), 0.5, _NEGRO),
    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 2),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ("LEFTPADDING", (0, 0), (-1, -1), 3),
    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
]


def _fila_seccion_roja(texto: str) -> Table:
    W = letter[0] - 3.0 * cm
    t = Table([[_p(texto, _HEADER_BLANCO)]], colWidths=[W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _ROJO),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, _NEGRO),
    ]))
    return t


# ── Bloque de información del grupo ──────────────────────────────────────────

def _bloque_info(docente_info: dict, periodo: str) -> Table:
    semestre, anio = _periodo_a_semestre_anio(periodo)
    nombre = _v(docente_info.get("nombre")) if docente_info else "Fredy Humberto Vera Rivera"
    W = letter[0] - 3.0 * cm

    sub1 = Table([
        [_p("DIRECTOR", _LABEL), _p(nombre, _VALOR),
         _p("Departamento", _LABEL), _p("Sistemas e informática", _VALOR)],
    ], colWidths=[1.8 * cm, 5.2 * cm, 2.4 * cm, 3.7 * cm],
        style=TableStyle(list(_BORDE_BASE)))

    sub2 = Table([
        [_p("Facultad", _LABEL), _p("Ingeniería", _VALOR)],
    ], colWidths=[2.0 * cm, W - 2.0 * cm],
        style=TableStyle(list(_BORDE_BASE)))

    sub3 = Table([
        [_p("Semestre Académico", _LABEL), _p(semestre, _VALOR),
         _p("Año", _LABEL), _p(anio, _VALOR)],
    ], colWidths=[3.8 * cm, 2.5 * cm, 1.2 * cm, W - 7.5 * cm],
        style=TableStyle(list(_BORDE_BASE)))

    data = [
        [_p("Nombre del Grupo de Investigación", _LABEL),
         _p("Grupo de investigación en Inteligencia Artificial - GIA", _VALOR)],
        [sub1],
        [sub2],
        [sub3],
    ]
    col1_w = 4.0 * cm
    t = Table(data, colWidths=[col1_w, W - col1_w])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, 0), 0.5, _NEGRO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("SPAN", (0, 1), (1, 1)),
        ("SPAN", (0, 2), (1, 2)),
        ("SPAN", (0, 3), (1, 3)),
        ("LEFTPADDING", (0, 1), (1, 3), 0),
        ("RIGHTPADDING", (0, 1), (1, 3), 0),
        ("TOPPADDING", (0, 1), (1, 3), 0),
        ("BOTTOMPADDING", (0, 1), (1, 3), 0),
    ]))
    return t


# ── Bloque por línea de investigación / proyecto ──────────────────────────────

def _bloque_linea(num: int, proyecto: dict) -> list:
    """Genera el bloque de una línea de investigación (1 por proyecto)."""
    W = letter[0] - 3.0 * cm
    elementos = []

    elementos.append(_fila_seccion_roja(f"{num}. LINEA DE INVESTIGACIÓN"))

    linea = _v(proyecto.get("fuente")) or "Sistemas Inteligentes Aplicados"
    nombre_proy_raw = _v(proyecto.get("proyecto")) or ""
    # Truncar nombres muy largos (CvLAC a veces pone el resumen como título)
    nombre_proy = nombre_proy_raw[:200] + ("..." if len(nombre_proy_raw) > 200 else "")
    responsable = _v(proyecto.get("docente")) or ""

    info_data = [
        [_p("Línea de Investigación", _LABEL), _p(linea, _VALOR)],
        [_p("Líder de la línea de Investigación", _LABEL), _p(responsable, _VALOR)],
        [_p("Proyecto a Ejecutar", _LABEL), _p(nombre_proy, _VALOR)],
        [_p("Responsable del Proyecto", _LABEL), _p(responsable, _VALOR)],
    ]
    t_info = Table(info_data, colWidths=[4.2 * cm, W - 4.2 * cm])
    t_info.setStyle(TableStyle(list(_BORDE_BASE)))
    elementos.append(t_info)

    # Tabla Objetivo | Actividades | Responsable | Producto (*)
    # Usamos UNA sola fila de datos (sin SPAN) para evitar rows gigantes.
    # Las actividades se unen con saltos de línea dentro de la celda.
    cw_obj = [W * 0.28, W * 0.36, W * 0.16, W * 0.20]
    actividades = _split_actividades(proyecto.get("descripcion"))
    actvs_html = "<br/>".join(_e(a) for a in actividades if a)
    # Truncar objetivo a 180 chars para que la celda quepa en la página
    objetivo_celda = nombre_proy[:180] + ("..." if len(nombre_proy) > 180 else "")

    header_row = [
        _p("Objetivo", _HEADER_NEGRO),
        _p("Actividades", _HEADER_NEGRO),
        _p("Responsable", _HEADER_NEGRO),
        _p("Producto (*)", _HEADER_NEGRO),
    ]
    data_row = [
        _p(objetivo_celda),
        Paragraph(actvs_html, _NORMAL),
        _p(responsable),
        _p(""),
    ]

    t_obj = Table([header_row, data_row], colWidths=cw_obj)
    t_obj.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _NARANJA),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    elementos.append(t_obj)

    elementos.append(Paragraph(
        "<b>Nota:</b> Se debe diligenciar la plantilla por cada proyecto de investigación "
        "que el grupo espera desarrollar y que se encuentre asociado a la línea de investigación.",
        _NOTA,
    ))
    elementos.append(Spacer(1, 0.2 * cm))

    return elementos


# ── Sección 2: Participación en Dirección ─────────────────────────────────────

def _seccion_participacion() -> list:
    W = letter[0] - 3.0 * cm
    elementos = []
    elementos.append(_fila_seccion_roja("2. PARTICIPACIÓN EN DIRECCIÓN DE"))

    # Sub-encabezado con tipos de grado
    sub_data = [[
        _p("Trabajo de Grado", _BOLD),
        _p("Pregrado [X]  Especializaciones [ ]", _VALOR),
        _p("Tesis", _BOLD),
        _p("Maestría [X]  Doctorado [ ]", _VALOR),
    ]]
    cw_sub = [2.8 * cm, 5.0 * cm, 1.5 * cm, 3.8 * cm]
    t_sub = Table(sub_data, colWidths=cw_sub)
    t_sub.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (0, 0), _NARANJA),
        ("BACKGROUND", (2, 0), (2, 0), _NARANJA),
    ]))
    elementos.append(t_sub)

    cw = [W * 0.28, W * 0.22, W * 0.16, W * 0.18, W * 0.16]
    header = [
        _p("Título del Proyecto", _HEADER_NEGRO),
        _p("Nombre del Estudiante", _HEADER_NEGRO),
        _p("Director", _HEADER_NEGRO),
        _p("Programa Académico", _HEADER_NEGRO),
        _p("Institución", _HEADER_NEGRO),
    ]
    filas = [header] + [[_p(""), _p(""), _p(""), _p(""), _p("")] for _ in range(6)]
    t = Table(filas, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("MINROWHEIGHT", (0, 1), (-1, -1), 0.55 * cm),
    ]))
    elementos.append(t)
    return elementos


# ── Sección 3: Eventos ────────────────────────────────────────────────────────

def _seccion_eventos() -> list:
    W = letter[0] - 3.0 * cm
    elementos = []
    elementos.append(_fila_seccion_roja("3. ORGANIZACIÓN DE EVENTOS DE INVESTIGACIÓN /CIENTÍFICOS"))

    cw = [W * 0.26, W * 0.16, W * 0.22, W * 0.20, W * 0.16]
    header = [
        _p("Nombre de Evento", _HEADER_NEGRO),
        _p("Fecha de realización", _HEADER_NEGRO),
        _p("Responsable", _HEADER_NEGRO),
        _p("Institución Promotora", _HEADER_NEGRO),
        _p("Entidades Participantes", _HEADER_NEGRO),
    ]
    filas = [header] + [[_p(""), _p(""), _p(""), _p(""), _p("")] for _ in range(4)]
    t = Table(filas, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("MINROWHEIGHT", (0, 1), (-1, -1), 0.55 * cm),
    ]))
    elementos.append(t)
    return elementos


# ── Sección 4: Otras Actividades ──────────────────────────────────────────────

def _seccion_otras() -> list:
    W = letter[0] - 3.0 * cm
    elementos = []
    elementos.append(_fila_seccion_roja("4. OTRAS ACTIVIDADES DE INVESTIGACIÓN (*)"))

    cw = [W * 0.32, W * 0.24, W * 0.24, W * 0.20]
    header = [
        _p("Nombre", _HEADER_NEGRO),
        _p("Responsable", _HEADER_NEGRO),
        _p("Fecha de realización", _HEADER_NEGRO),
        _p("Producto", _HEADER_NEGRO),
    ]
    filas_default = [
        [_p("Coordinación Semillero SIA"), _p(""), _p(""), _p("Informe de Gestión del Semillero SIA")],
        [_p("Participación en Eventos Académicos"), _p("Miembros GIA"), _p(""), _p("Participación como ponente, charlas, talleres, conferencias, cursos, webinars u otros eventos académicos")],
        [_p("Actualizaciones"), _p("Miembros GIA"), _p(""), _p("Certificados de Talleres, cursos, webinars, MOOC")],
        [_p("Reunión mensual de avances GIA"), _p("Miembros GIA"), _p(""), _p("Actas de Reunión del Grupo GIA")],
    ]
    filas = [header] + filas_default + [[_p(""), _p(""), _p(""), _p("")] for _ in range(2)]
    t = Table(filas, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    elementos.append(t)
    elementos.append(Spacer(1, 0.2 * cm))
    elementos.append(Paragraph(
        "(*) <b>Actividades de investigación:</b> Espacios de socialización, Foros, "
        "Reuniones, Elaboración de documentos, entre otros.",
        _NOTA,
    ))
    return elementos


# ── Sección de firma ──────────────────────────────────────────────────────────

def _seccion_firma(docente_info: dict) -> list:
    W = letter[0] - 3.0 * cm
    nombre = _v(docente_info.get("nombre")) if docente_info else "Fredy Humberto Vera Rivera"
    elementos = []
    elementos.append(Spacer(1, 0.4 * cm))

    data = [
        [_p("ELABORÓ:", _HEADER_NEGRO), _p("REVISÓ:", _HEADER_NEGRO)],
        [_p("Director Grupo de Investigación", _HEADER_NEGRO),
         _p("Vo Bo. Docente Representante de Investigación de la Facultad:", _HEADER_NEGRO)],
        [_p("Nombre Completo", _LABEL), _p("Nombre Completo", _LABEL)],
        [_p(nombre), _p("Luis Emilio Vera")],
        [_p("Firma", _LABEL), _p("Firma", _LABEL)],
        [_p(""), _p("")],
    ]
    t = Table(data, colWidths=[W / 2, W / 2])
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _ROJO),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BLANCO),
        ("BACKGROUND", (0, 1), (-1, 1), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 1), "CENTER"),
        ("MINROWHEIGHT", (0, 4), (-1, 5), 1.2 * cm),
    ]))
    elementos.append(t)
    return elementos


# ── Función pública ───────────────────────────────────────────────────────────

def generar_pdf_fo_in_17_plantilla(resultado: dict) -> str:
    """
    Genera PDF fiel al formato oficial FO-IN-17 a partir del dict de extraer_proyectos().
    Retorna la ruta absoluta del archivo generado.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docente_info = resultado.get("docente") or {}
    nombre_docente = _v(docente_info.get("nombre")) or "docente"
    nombre_slug = re.sub(r"[^\w]", "_", nombre_docente)[:25].strip("_")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"FO-IN-17_{nombre_slug}_{ts}.pdf"
    ruta = OUTPUT_DIR / nombre_archivo

    periodo = _v(resultado.get("periodo")) or "2026-1"
    fecha_doc = datetime.now().strftime("%d/%m/%Y")
    proyectos = resultado.get("proyectos", [])
    proyectos_validos = [p for p in proyectos if p.get("proyecto") and not p.get("error")]

    W, H = letter
    margen_lat = 1.5 * cm
    margen_top = 3.2 * cm
    margen_bot = 1.2 * cm

    doc = BaseDocTemplate(
        str(ruta),
        pagesize=letter,
        rightMargin=margen_lat,
        leftMargin=margen_lat,
        topMargin=margen_top,
        bottomMargin=margen_bot,
    )
    frame = Frame(
        margen_lat, margen_bot,
        W - 2 * margen_lat, H - margen_top - margen_bot,
        id="main",
    )
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame])])

    story: list = []
    story.append(_bloque_info(docente_info, periodo))
    story.append(Spacer(1, 0.3 * cm))

    # Una sección por proyecto
    if proyectos_validos:
        for i, proj in enumerate(proyectos_validos, 1):
            for e in _bloque_linea(i, proj):
                story.append(e)
    else:
        story.append(_fila_seccion_roja("1. LINEA DE INVESTIGACIÓN"))
        story.append(Paragraph("No se encontraron proyectos en las fuentes consultadas.", _INTRO))
        story.append(Spacer(1, 0.3 * cm))

    # Nota general
    story.append(Paragraph(
        "(*) <b>Los productos de investigación deben ser acorde con los enunciados en el "
        "Acuerdo que adopta el Sistema de Investigación de la Universidad Francisco de Paula "
        "Santander:</b> artículo publicado o remitido a una revista indexada o avalada por "
        "la UFPS, ponencia, software, prototipo, diseño industrial, procesos o técnicas, "
        "libros, capítulos de libro.",
        _NOTA,
    ))
    story.append(Spacer(1, 0.4 * cm))

    for e in _seccion_participacion():
        story.append(e)
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph(
        "<b>Nota:</b> Los numerales 1 y 2, se deben diligenciar por cada línea de "
        "investigación perteneciente al grupo.",
        _NOTA,
    ))
    story.append(Spacer(1, 0.4 * cm))

    for e in _seccion_eventos():
        story.append(e)
    story.append(Spacer(1, 0.4 * cm))

    for e in _seccion_otras():
        story.append(e)

    for e in _seccion_firma(docente_info):
        story.append(e)

    def _canvas_factory(*args, **kwargs):
        return _EncabezadoCanvas(
            *args,
            titulo_doc="PLAN DE ACCIÓN GRUPOS DE INVESTIGACIÓN",
            codigo="FO-IN-17",
            fecha_doc=fecha_doc,
            **kwargs,
        )

    doc.build(story, canvasmaker=_canvas_factory)
    return str(ruta)
