"""
Generador de PDF con formato fiel al FO-IN-13 oficial.
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
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "generados"
LOGO_PATH = Path(__file__).resolve().parent.parent / "static" / "docs" / "Logo UFPS.png"
# Firma del director extraída del FO-IN-17 oficial (ver services/extraer_firma.py).
# Provisional hasta obtener la firma del líder actual del semillero.
FIRMA_PATH = Path(__file__).resolve().parent.parent / "static" / "docs" / "firma_director.png"

# Director del grupo GIA (constante del formato oficial, no del docente solicitado)
DIRECTOR_GRUPO = "Fredy Humberto Vera Rivera"

# ── Colores del formulario oficial ───────────────────────────────────────────
_ROJO = colors.HexColor("#C00000")
_NARANJA = colors.HexColor("#F4B942")
_GRIS_CLARO = colors.HexColor("#F2F2F2")
_NEGRO = colors.black
_BLANCO = colors.white

# ── Estilos de párrafo ────────────────────────────────────────────────────────
_BASE = getSampleStyleSheet()

_NORMAL = ParagraphStyle(
    "FO13Normal", parent=_BASE["Normal"],
    fontName="Helvetica", fontSize=8, leading=10, spaceAfter=0,
)
_BOLD = ParagraphStyle(
    "FO13Bold", parent=_NORMAL,
    fontName="Helvetica-Bold",
)
_HEADER_BLANCO = ParagraphStyle(
    "FO13HdrBlanco", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=9, textColor=_BLANCO, alignment=1,
)
_HEADER_NEGRO = ParagraphStyle(
    "FO13HdrNegro", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=8, textColor=_NEGRO, alignment=1,
)
_TITLE_GRANDE = ParagraphStyle(
    "FO13TitleGrande", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=11, textColor=_BLANCO, alignment=1,
)
_LABEL = ParagraphStyle(
    "FO13Label", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=8,
)
_VALOR = ParagraphStyle(
    "FO13Valor", parent=_NORMAL,
    fontName="Helvetica", fontSize=8,
)
_FOOTER = ParagraphStyle(
    "FO13Footer", parent=_NORMAL,
    fontName="Helvetica-Bold", fontSize=7, alignment=1,
)
_FOOTER_ITALIC = ParagraphStyle(
    "FO13FooterItalic", parent=_FOOTER,
    fontName="Helvetica-Oblique",
)
_INTRO = ParagraphStyle(
    "FO13Intro", parent=_NORMAL,
    fontName="Helvetica", fontSize=8, leading=11,
)
_NOTA = ParagraphStyle(
    "FO13Nota", parent=_NORMAL,
    fontName="Helvetica-Oblique", fontSize=7.5, leading=10,
)


def _e(v) -> str:
    """Escapa XML para Paragraph."""
    if v is None:
        return ""
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _v(v) -> str:
    if v is None or str(v).strip() == "":
        return ""
    return str(v).strip()


def _split_actividades(desc: str | None, max_chars: int = 180, max_items: int = 5) -> list[str]:
    """Divide descripción en actividades cortas (límites para evitar rows demasiado altos)."""
    if not desc or not desc.strip():
        return [""]
    texto = re.sub(r"\r\n|\r", "\n", desc.strip())
    lineas = [ln.strip() for ln in texto.split("\n") if ln.strip()]

    resultado: list[str] = []
    for linea in lineas:
        if len(linea) > max_chars:
            linea = linea[:max_chars].rsplit(" ", 1)[0] + "..."
        resultado.append(linea)

    return resultado[:max_items] if resultado else [""]


def _normalizar_actividades(actividades, max_chars: int = 180, max_items: int = 5) -> list[str]:
    """Normaliza la lista de actividades ya estructurada. Acepta lista o string."""
    if isinstance(actividades, str):
        return _split_actividades(actividades, max_chars, max_items)
    if not actividades:
        return [""]
    resultado: list[str] = []
    for act in actividades:
        a = str(act).strip()
        if not a:
            continue
        if len(a) > max_chars:
            a = a[:max_chars].rsplit(" ", 1)[0] + "..."
        resultado.append(a)
    return resultado[:max_items] if resultado else [""]


def _periodo_a_semestre_anio(periodo: str) -> tuple[str, str]:
    """'2026-1' → ('1', '2026')"""
    m = re.match(r"(\d{4})[-–/]([12])", periodo or "")
    if m:
        return m.group(2), m.group(1)
    return "1", str(datetime.now().year)


# ── Clase canvas con encabezado y pie de página ───────────────────────────────

class _EncabezadoCanvas(rl_canvas.Canvas):
    """Canvas personalizado que dibuja el encabezado y pie en cada página."""

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
        h_hdr = 1.8 * cm  # altura bloque superior
        h_elab = 0.6 * cm  # altura fila ELABORÓ

        col1 = 3.2 * cm   # ancho columna logo
        col_mid = 9.0 * cm  # ancho columna título
        col_right_lbl = 1.6 * cm
        col_right_val = 2.0 * cm
        x0 = 1.5 * cm
        W_total = W - 3.0 * cm  # 1.5cm cada lado

        # ── Bloque superior (3 columnas) ─────────────────────────────────────
        # Columna izq: UFPS texto (sustituye al logo)
        self.setStrokeColor(_NEGRO)
        self.setLineWidth(0.5)

        # rectángulo columna logo
        self.rect(x0, top - h_hdr, col1, h_hdr)
        logo_ok = False
        if LOGO_PATH.exists():
            try:
                pad = 0.2 * cm
                self.drawImage(
                    str(LOGO_PATH),
                    x0 + pad, top - h_hdr + pad,
                    width=col1 - 2 * pad, height=h_hdr - 2 * pad,
                    preserveAspectRatio=True, mask="auto",
                )
                logo_ok = True
            except Exception:
                logo_ok = False
        if not logo_ok:
            self.setFont("Helvetica-Bold", 7)
            self.setFillColor(_NEGRO)
            self.drawCentredString(x0 + col1 / 2, top - 0.5 * cm, "UF")
            self.drawCentredString(x0 + col1 / 2, top - 0.9 * cm, "PS")
            self.setFont("Helvetica", 5.5)
            self.drawCentredString(x0 + col1 / 2, top - 1.2 * cm, "Universidad Francisco")
            self.drawCentredString(x0 + col1 / 2, top - 1.45 * cm, "de Paula Santander")

        # Columna central: INVESTIGACIÓN + título rojo
        x_mid = x0 + col1
        self.rect(x_mid, top - h_hdr, col_mid, h_hdr)
        self.setFont("Helvetica-Bold", 10)
        self.setFillColor(_NEGRO)
        self.drawCentredString(x_mid + col_mid / 2, top - 0.55 * cm, "INVESTIGACIÓN")

        # Barra roja con título del documento
        self.setFillColor(_ROJO)
        self.rect(x_mid, top - h_hdr + 0.0, col_mid, 0.8 * cm, fill=1, stroke=0)
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(_BLANCO)
        self.drawCentredString(x_mid + col_mid / 2, top - h_hdr + 0.25 * cm, self._titulo_doc)

        # Columna derecha: CÓDIGO / VERSIÓN / FECHA / PÁGINA
        x_right = x_mid + col_mid
        ancho_right = col_right_lbl + col_right_val
        fila_h = h_hdr / 4
        filas = [
            ("CÓDIGO", self._codigo),
            ("VERSIÓN", "01"),
            ("FECHA", self._fecha_doc),
            (f"PÁGINA", f"{pagina} de {total}"),
        ]
        for j, (lbl, val) in enumerate(filas):
            y_f = top - (j + 1) * fila_h
            self.setFillColor(_BLANCO)
            self.rect(x_right, y_f, ancho_right, fila_h, fill=0, stroke=1)
            self.setFont("Helvetica-Bold", 6.5)
            self.setFillColor(_NEGRO)
            self.drawString(x_right + 0.1 * cm, y_f + 0.1 * cm, lbl)
            self.setFont("Helvetica", 6.5)
            self.drawString(x_right + col_right_lbl + 0.05 * cm, y_f + 0.1 * cm, val)
            # separador entre label y val
            self.line(x_right + col_right_lbl, y_f, x_right + col_right_lbl, y_f + fila_h)

        # ── Fila ELABORÓ / REVISÓ / APROBÓ ───────────────────────────────────
        y_elab = top - h_hdr - h_elab
        col_e = W_total / 3
        etiquetas = ["ELABORÓ", "REVISÓ", "APROBÓ"]
        valores = ["Líder Investigación", "Equipo Operativo de Calidad", "Líder de Calidad"]
        for j in range(3):
            xj = x0 + j * col_e
            self.setFillColor(_BLANCO)
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


# ── Helpers de tabla ──────────────────────────────────────────────────────────

def _ts(*args) -> TableStyle:
    return TableStyle(list(args))


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


def _fila_seccion(texto: str) -> Table:
    """Fila roja de sección con texto blanco."""
    t = Table([[Paragraph(texto, _HEADER_BLANCO)]], colWidths=["100%"])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _ROJO),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.5, _NEGRO),
    ]))
    return t


def _p(txt: str, style=None) -> Paragraph:
    if style is None:
        style = _NORMAL
    return Paragraph(_e(txt), style)


# ── Constructor de secciones ──────────────────────────────────────────────────

def _bloque_info(docente_info: dict, periodo: str) -> Table:
    semestre, anio = _periodo_a_semestre_anio(periodo)
    # El DIRECTOR del grupo es constante del formato, no el docente solicitado
    nombre = DIRECTOR_GRUPO
    s1 = "[X]" if semestre == "1" else "[ ]"
    s2 = "[X]" if semestre == "2" else "[ ]"

    W = letter[0] - 3.0 * cm
    data = [
        [_p("GRUPO DE INVESTIGACIÓN", _LABEL),
         Paragraph("Grupo de investigación en Inteligencia Artificial", _VALOR)],
        [_p("DIRECTOR", _LABEL),
         _p(nombre or "Fredy Humberto Vera Rivera", _VALOR)],
        [Table([
            [_p("DEPARTAMENTO", _LABEL), _p("Sistemas e informática", _VALOR),
             _p("FACULTAD", _LABEL), _p("Ingeniería", _VALOR)]
         ], colWidths=[2.8 * cm, 5.5 * cm, 2.2 * cm, 3.5 * cm],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, _NEGRO),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ]))],
        [Table([
            [_p("SEMESTRE ACADÉMICO", _LABEL),
             Paragraph(f"PRIMER {s1}   SEGUNDO {s2}", _VALOR),
             _p("AÑO", _LABEL),
             _p(anio, _VALOR)]
         ], colWidths=[3.8 * cm, 4.2 * cm, 1.2 * cm, 4.8 * cm],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, _NEGRO),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ]))],
    ]
    # Para filas 1 y 2 necesito span completo (2 columnas → fusionar)
    t = Table(data, colWidths=[3.8 * cm, W - 3.8 * cm])
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.5, _NEGRO),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("SPAN", (0, 2), (1, 2)),  # Filas de sub-tablas ocupan ambas columnas
        ("SPAN", (0, 3), (1, 3)),
        ("LEFTPADDING", (0, 2), (1, 2), 0),
        ("RIGHTPADDING", (0, 2), (1, 2), 0),
        ("TOPPADDING", (0, 2), (1, 2), 0),
        ("BOTTOMPADDING", (0, 2), (1, 2), 0),
        ("LEFTPADDING", (0, 3), (1, 3), 0),
        ("RIGHTPADDING", (0, 3), (1, 3), 0),
        ("TOPPADDING", (0, 3), (1, 3), 0),
        ("BOTTOMPADDING", (0, 3), (1, 3), 0),
    ]
    t.setStyle(TableStyle(style_cmds))
    return t


def proyectos_validos(proyectos: list[dict]) -> list[dict]:
    """
    Filtra y ordena los proyectos que aparecen en la sección 1 del FO-IN-13.

    Es la fuente de verdad del conjunto/orden de proyectos: tanto el generador
    del PDF como el flujo conversacional de % de cumplimiento (routes/chat.py)
    deben usar exactamente esta misma lista para que las preguntas y las celdas
    coincidan.
    """
    return [p for p in (proyectos or []) if p.get("proyecto") and not p.get("error")][:6]


def _seccion_proyectos(proyectos: list[dict], cumplimientos: dict | None = None) -> list:
    """Genera la tabla de la sección 1: Proyectos de Investigación.

    `cumplimientos` mapea la **posición** del proyecto en `proyectos_validos`
    (índice 0..N) a su porcentaje, p. ej. {0: "90%", 1: "80%"}. Se usa índice y
    no el título porque pueden existir proyectos con títulos repetidos o vacíos.
    """
    cumplimientos = cumplimientos or {}
    W = letter[0] - 3.0 * cm
    cw = [W * 0.38, W * 0.48, W * 0.14]

    elementos = []
    elementos.append(_fila_seccion("1. Proyectos de Investigación"))

    # Construir filas de datos
    data: list[list] = [
        [_p("Proyecto", _HEADER_NEGRO),
         _p("Actividades", _HEADER_NEGRO),
         _p("% de Cumplimiento", _HEADER_NEGRO)],
    ]
    spans: list[tuple] = []
    row_idx = 1  # empezamos en fila 1 (después del header)

    proyectos_filtrados = proyectos_validos(proyectos)
    if not proyectos_filtrados:
        data.append([_p("Sin proyectos identificados en las fuentes consultadas."), "", ""])
        spans.append(("SPAN", (0, 1), (2, 1)))
    else:
        for pos, proj in enumerate(proyectos_filtrados):
            actividades = _normalizar_actividades(proj.get("actividades"))
            n = max(len(actividades), 1)
            nombre_raw = _v(proj.get("proyecto")) or ""
            nombre_truncado = nombre_raw[:200] + ("..." if len(nombre_raw) > 200 else "")
            # % cumplimiento indicado por el docente (clave = posición del proyecto)
            pct = cumplimientos.get(pos, cumplimientos.get(str(pos), ""))
            # Primera fila del proyecto
            data.append([
                _p(nombre_truncado),
                _p(actividades[0] if actividades else ""),
                _p(pct),
            ])
            # Filas adicionales para más actividades
            for k in range(1, n):
                data.append([
                    _p(""),  # celda vacía (span cubrirá)
                    _p(actividades[k]),
                    _p(""),  # celda vacía (span cubrirá)
                ])
            if n > 1:
                spans.append(("SPAN", (0, row_idx), (0, row_idx + n - 1)))
                spans.append(("SPAN", (2, row_idx), (2, row_idx + n - 1)))
            row_idx += n

    style_cmds = list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ] + spans
    t = Table(data, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(style_cmds))
    elementos.append(t)
    return elementos


def _seccion_tg(proyectos: list[dict]) -> list:
    """Sección 2: Dirección de Trabajo de Grado."""
    W = letter[0] - 3.0 * cm
    cw = [W * 0.60, W * 0.27, W * 0.13]

    elementos = []
    elementos.append(_fila_seccion("2. Participación en Dirección de Trabajo de Grado y/o Tesis"))

    header = [
        _p("Título del Proyecto", _HEADER_NEGRO),
        _p("Director", _HEADER_NEGRO),
        _p("% de Cumplimiento", _HEADER_NEGRO),
    ]
    # Filas vacías para completar manualmente
    filas_vacias = [header] + [[_p(""), _p(""), _p("")] for _ in range(5)]
    t = Table(filas_vacias, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("MINROWHEIGHT", (0, 1), (-1, -1), 0.5 * cm),
    ]))
    elementos.append(t)
    return elementos


def _seccion_eventos() -> list:
    """Sección 3: Organización de Eventos."""
    W = letter[0] - 3.0 * cm
    cw = [W * 0.60, W * 0.27, W * 0.13]

    elementos = []
    elementos.append(_fila_seccion("3. Organización de Eventos de Investigación /Científicos"))

    header = [
        _p("Nombre de Evento", _HEADER_NEGRO),
        _p("Fecha de realización", _HEADER_NEGRO),
        _p("% de Cumplimiento", _HEADER_NEGRO),
    ]
    filas = [header] + [[_p(""), _p(""), _p("")] for _ in range(4)]
    t = Table(filas, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
        ("MINROWHEIGHT", (0, 1), (-1, -1), 0.5 * cm),
    ]))
    elementos.append(t)
    return elementos


def _seccion_otras() -> list:
    """Sección 4: Otras Actividades."""
    W = letter[0] - 3.0 * cm
    cw = [W * 0.55, W * 0.30, W * 0.15]

    elementos = []
    elementos.append(_fila_seccion("4. Otras Actividades de Investigación (*)"))

    header = [
        _p("Nombre", _HEADER_NEGRO),
        _p("Tipo de Actividad", _HEADER_NEGRO),
        _p("% de Cumplimiento", _HEADER_NEGRO),
    ]
    filas_default = [
        [_p("Coordinación Semillero SIA"), _p("Formación"), _p("")],
        [_p("Participación en Eventos Académicos"), _p("Investigación"), _p("")],
        [_p("Actualizaciones"), _p("Formación"), _p("")],
        [_p("Reunión mensual de avances GIA"), _p("Organización"), _p("")],
    ]
    filas = [header] + filas_default + [[_p(""), _p(""), _p("")] for _ in range(2)]
    t = Table(filas, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 0), (2, -1), "CENTER"),
    ]))
    elementos.append(t)
    elementos.append(Spacer(1, 0.2 * cm))
    elementos.append(Paragraph(
        "(*) <b>Actividades de investigación:</b> Espacios de socialización, Foros, "
        "Reuniones, Elaboración de documentos, entre otros.",
        _NOTA,
    ))
    return elementos


def _seccion_productos(proyectos: list[dict]) -> list:
    """Tabla de productos (páginas 4-6 del original)."""
    W = letter[0] - 3.0 * cm
    cw = [W * 0.22, W * 0.44, W * 0.18, W * 0.16]

    elementos = []
    elementos.append(Spacer(1, 0.4 * cm))
    elementos.append(Paragraph(
        "A continuación, describa los productos obtenidos por el grupo de investigación "
        "en el semestre actual, según lo establecido en el Acuerdo que adopta el Sistema "
        "de Investigación de la Universidad Francisco de Paula Santander.",
        _INTRO,
    ))
    elementos.append(Spacer(1, 0.3 * cm))

    def _instruccion(txt: str) -> Paragraph:
        return Paragraph(f"<b>Instrucción:</b> {_e(txt)}", ParagraphStyle(
            "Inst", parent=_NORMAL, fontSize=7.5, textColor=colors.HexColor("#555555"),
        ))

    header = [
        _p("PRODUCTO", _BOLD),
        _p("DESCRIPCIÓN", _BOLD),
        _p("RESPONSABLE", _BOLD),
        _p("FECHA", _BOLD),
    ]

    articulos = [p for p in proyectos if p.get("proyecto") and not p.get("error")]

    def _fila_prod(producto, descripcion, responsable="", fecha=""):
        return [_p(producto), _p(descripcion), _p(responsable), _p(fecha)]

    rows = [header]

    # Actualización GrupLAC
    rows.append([_p("Actualización GrupLAC\nActualización CGIS", _LABEL),
                 _instruccion("Escribir el(los) integrante(s), proyecto(s) o producto(s) que se han actualizado en el semestre académico."),
                 _p(""), _p("")])
    rows.append([_p(""), _p("Se actualizó el CVLAC de cada uno de los investigadores del grupo."),
                 _p("Investigadores del grupo"), _p("")])

    # Participación convocatoria Minciencias
    rows.append([_p("Participación convocatoria de reconocimiento Minciencias", _LABEL),
                 _instruccion("Se debe escribir el nombre de la Convocatoria de reconocimiento Minciencias en la cual ha participado. (Si aplica)"),
                 _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])

    # Proyectos terminados
    rows.append([_p("Proyectos terminados y/o ejecución, avalados con financiación interna (FINU) o externa.", _LABEL),
                 _instruccion("Se debe escribir el nombre de el(los) proyecto(s) en ejecución o terminados(s), que no hayan sido considerados en el plan de acción del semestre actual."),
                 _p(""), _p("")])
    for proj in articulos[:3]:
        rows.append([_p(""), _p(_v(proj.get("proyecto"))),
                     _p(_v(proj.get("responsable") or "")), _p("")])

    # Artículo publicado
    rows.append([_p("Artículo publicado o remitido revista científica", _LABEL),
                 _instruccion("Se debe escribir el nombre de el(los) artículos publicado(s) o remitido(s) a revista científica."),
                 _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])

    # Participación propuesta
    rows.append([_p("Participación propuesta investigación en convocatoria interna o externa", _LABEL),
                 _instruccion("Se debe escribir el nombre de la(s) propuesta(s) y el nombre de la(s) convocatoria(s) interna o externa en la cual participo el Grupo."),
                 _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])

    # Ponencia
    rows.append([_p("Ponencia evento académico regional, nacional o internacional", _LABEL),
                 _instruccion("Se debe escribir el nombre de la(s) ponencia(s), nacional o internacional, en la cual participo el grupo."),
                 _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])

    # Dirección TG
    rows.append([_p("Dirección trabajo de grado (post-grado, maestría)", _LABEL),
                 _instruccion("Se debe escribir el nombre de: el (los) proyecto(s), que se está dirigiendo como trabajo de grado (post-grado, maestría)"),
                 _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])

    # Otros
    rows.append([_p("Otros productos", _LABEL),
                 _instruccion("Se debe escribir el nombre de: el(los) producto(s), fecha."),
                 _p(""), _p("")])
    rows.append([_p(""), _p(""), _p(""), _p("")])

    # Filas instrucción con fondo gris
    idx_instrucciones = [1, 3, 5, 9, 11, 13, 15, 17]
    style_cmds = list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (3, 0), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for idx in idx_instrucciones:
        if idx < len(rows):
            style_cmds.append(("BACKGROUND", (1, idx), (3, idx), _GRIS_CLARO))

    t = Table(rows, colWidths=cw, repeatRows=1)
    t.setStyle(TableStyle(style_cmds))
    elementos.append(t)
    return elementos


def _firma_flowable(alto_cm: float = 1.0):
    """
    Devuelve un Image con la firma del director escalada para caber en la celda
    de firma. Si el PNG no existe, devuelve un párrafo vacío (fallback seguro).
    """
    if not FIRMA_PATH.exists():
        return _p("")
    try:
        img = Image(str(FIRMA_PATH))
        ratio = (img.imageWidth / img.imageHeight) if img.imageHeight else 3.0
        img.drawHeight = alto_cm * cm
        img.drawWidth = alto_cm * cm * ratio
        img.hAlign = "CENTER"
        return img
    except Exception:
        return _p("")


def _seccion_firma(docente_info: dict) -> list:
    """Sección final: ELABORADO POR / REVISADO POR."""
    W = letter[0] - 3.0 * cm
    elementos = []
    elementos.append(Spacer(1, 0.3 * cm))
    elementos.append(Paragraph(
        "<b>NOTA:</b> Los productos a evaluar son los establecidos en el Acuerdo que adopta "
        "el Sistema de Investigación de la Universidad Francisco de Paula Santander. "
        "Se solicita anexar los soportes de los productos registrados en el periodo académico, "
        "solo se tendrá en cuenta para el reconocimiento de las horas de Investigación los "
        "productos que presenten su respectivo soporte.",
        _NOTA,
    ))
    elementos.append(Spacer(1, 0.4 * cm))

    # Quien firma como Director del Grupo es constante del formato
    nombre = DIRECTOR_GRUPO

    data = [
        [_p("ELABORADO POR", _HEADER_NEGRO), _p("REVISADO POR", _HEADER_NEGRO)],
        [_p("Director Grupo de Investigación", _HEADER_NEGRO),
         _p("VoBo. Representante de la Facultad ante el CCIE:", _HEADER_NEGRO)],
        [_p("NOMBRE:", _LABEL), _p("NOMBRE:", _LABEL)],
        [_p(nombre), _p("Luis Emilio Vera")],
        [_p("FIRMA:", _LABEL), _p("FIRMA:", _LABEL)],
        # Firma del director en ELABORADO POR; REVISADO POR se deja en blanco.
        [_firma_flowable(), _p("")],
    ]
    cw = [W / 2, W / 2]
    style_cmds = list(_BORDE_BASE) + [
        ("BACKGROUND", (0, 0), (-1, 0), _ROJO),
        ("TEXTCOLOR", (0, 0), (-1, 0), _BLANCO),
        ("BACKGROUND", (0, 1), (-1, 1), _GRIS_CLARO),
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 1), "CENTER"),
        ("MINROWHEIGHT", (0, 4), (-1, 5), 1.2 * cm),
        ("VALIGN", (0, 5), (-1, 5), "MIDDLE"),
    ]
    t = Table(data, colWidths=cw)
    t.setStyle(TableStyle(style_cmds))
    elementos.append(t)
    return elementos


# ── Función pública ───────────────────────────────────────────────────────────

def generar_pdf_fo_in_13_plantilla(resultado: dict) -> str:
    """
    Genera PDF fiel al formato oficial FO-IN-13 a partir del dict de extraer_proyectos().
    Retorna la ruta absoluta del archivo generado.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docente_info = resultado.get("docente") or {}
    # Archivo único y estable (responsable + periodo); el nombre oficial de
    # descarga lo fija la ruta /descargar vía Content-Disposition.
    responsable_doc = _v(resultado.get("responsable")) or _v(docente_info.get("nombre")) or "docente"
    nombre_slug = re.sub(r"[^\w]", "_", responsable_doc)[:25].strip("_")
    periodo = _v(resultado.get("periodo")) or "2026-1"
    periodo_slug = re.sub(r"[^\w]", "_", periodo)
    nombre_archivo = f"FO-IN-13_{nombre_slug}_{periodo_slug}.pdf"
    ruta = OUTPUT_DIR / nombre_archivo
    fecha_ext = resultado.get("fecha_extraccion", "")
    fecha_doc = datetime.now().strftime("%d/%m/%Y")
    proyectos = resultado.get("proyectos", [])
    cumplimientos = resultado.get("cumplimientos") or {}

    # ── Márgenes: espacio para encabezado (2.9cm) + contenido ────────────────
    W, H = letter
    margen_lat = 1.5 * cm
    margen_top = 3.2 * cm   # espacio para encabezado dibujado por canvas
    margen_bot = 1.2 * cm   # espacio para pie

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

    # ── Contenido ─────────────────────────────────────────────────────────────
    story: list = []

    # Bloque de información del grupo
    story.append(_bloque_info(docente_info, periodo))
    story.append(Spacer(1, 0.4 * cm))

    # Texto introductorio
    story.append(Paragraph(
        "Teniendo en cuenta el plan de acción presentado para desarrollar en el semestre "
        "académico actual, indique frente a cada producto el porcentaje de cumplimiento.",
        _INTRO,
    ))
    story.append(Spacer(1, 0.3 * cm))

    # Sección 1: Proyectos
    for e in _seccion_proyectos(proyectos, cumplimientos):
        story.append(e)
    story.append(Spacer(1, 0.4 * cm))

    # Sección 2: Dirección TG
    for e in _seccion_tg(proyectos):
        story.append(e)
    story.append(Spacer(1, 0.4 * cm))

    # Sección 3: Eventos
    for e in _seccion_eventos():
        story.append(e)
    story.append(Spacer(1, 0.4 * cm))

    # Sección 4: Otras Actividades
    for e in _seccion_otras():
        story.append(e)

    # Tabla de productos (página 4+)
    for e in _seccion_productos(proyectos):
        story.append(e)

    # Firma final
    for e in _seccion_firma(docente_info):
        story.append(e)

    # ── Build con canvas personalizado ────────────────────────────────────────
    def _canvas_factory(*args, **kwargs):
        return _EncabezadoCanvas(
            *args,
            titulo_doc="INFORME DE GESTIÓN DE GRUPOS DE INVESTIGACIÓN",
            codigo="FO-IN-13",
            fecha_doc=fecha_doc,
            **kwargs,
        )

    doc.build(story, canvasmaker=_canvas_factory)
    return str(ruta)
