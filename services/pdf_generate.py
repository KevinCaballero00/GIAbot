from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "static" / "generados"


def generar_pdf_fo_in_13(resultado: dict) -> str:
    """
    Genera un PDF del FO-IN-13 a partir del resultado normalizado de extraer_proyectos.
    Retorna la ruta absoluta del archivo generado.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    docente_info = resultado.get("docente") or {}
    nombre_docente = docente_info.get("nombre", "Desconocido")
    nombre_slug = "".join(
        c if c.isalnum() else "_" for c in nombre_docente
    )[:20].strip("_")
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

    styles = getSampleStyleSheet()
    color_institucional = colors.HexColor("#1a3a5c")

    estilo_titulo = ParagraphStyle(
        "Titulo",
        parent=styles["Title"],
        fontSize=13,
        spaceAfter=4,
        textColor=color_institucional,
    )
    estilo_h2 = ParagraphStyle(
        "H2",
        parent=styles["Heading2"],
        fontSize=11,
        spaceBefore=8,
        spaceAfter=4,
        textColor=color_institucional,
    )
    estilo_normal = styles["Normal"]
    estilo_meta = ParagraphStyle(
        "Meta",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.grey,
    )

    content: list = []

    # Encabezado
    content.append(Paragraph(
        "FO-IN-13 – Informe de Gestión de Grupos de Investigación",
        estilo_titulo,
    ))
    content.append(Paragraph(
        "Grupo GIA – Universidad Francisco de Paula Santander",
        estilo_normal,
    ))
    content.append(HRFlowable(
        width="100%", thickness=1, color=colors.HexColor("#cccccc"),
    ))
    content.append(Spacer(1, 0.3 * cm))

    # Datos del informe
    periodo = resultado.get("periodo", "N/D")
    fecha_ext = resultado.get("fecha_extraccion", "")
    fecha_formato = fecha_ext[:10] if fecha_ext else "N/D"

    tabla_datos = [
        ["Docente:", nombre_docente],
        ["Período:", periodo],
        ["Fecha de extracción:", fecha_formato],
    ]
    t = Table(tabla_datos, colWidths=[4 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    content.append(t)
    content.append(Spacer(1, 0.5 * cm))

    # Proyectos
    proyectos = resultado.get("proyectos", [])
    proyectos_validos = [p for p in proyectos if p.get("proyecto") and not p.get("error")]

    content.append(Paragraph("Proyectos Identificados", estilo_h2))

    if proyectos_validos:
        for i, p in enumerate(proyectos_validos, 1):
            titulo_p = p.get("proyecto", "Sin título")
            content.append(Paragraph(f"<b>{i}. {titulo_p}</b>", estilo_normal))

            detalles: list[str] = []
            if p.get("fuente"):
                detalles.append(f"Fuente: {p['fuente']}")
            if p.get("docente"):
                detalles.append(f"Docente: {p['docente']}")
            if p.get("periodo"):
                detalles.append(f"Período: {p['periodo']}")
            if detalles:
                content.append(Paragraph(
                    "   " + " | ".join(detalles),
                    estilo_meta,
                ))

            desc = (p.get("descripcion") or "").strip()
            if desc:
                desc_corta = desc[:500] + ("…" if len(desc) > 500 else "")
                content.append(Paragraph(f"   {desc_corta}", estilo_normal))

            content.append(Spacer(1, 0.25 * cm))
    else:
        content.append(Paragraph(
            "No se encontraron proyectos en las fuentes consultadas para este período.",
            estilo_normal,
        ))

    # Fuentes consultadas
    fuentes = resultado.get("fuentes_consultadas", [])
    if fuentes:
        content.append(Paragraph("Fuentes Consultadas", estilo_h2))
        for f in fuentes:
            content.append(Paragraph(f"• {f}", estilo_normal))

    # Errores / advertencias
    errores = resultado.get("errores", [])
    if errores:
        content.append(Spacer(1, 0.3 * cm))
        content.append(Paragraph("Advertencias", estilo_h2))
        for e in errores:
            content.append(Paragraph(f"* {e}", estilo_meta))

    # Pie
    content.append(Spacer(1, 0.5 * cm))
    content.append(HRFlowable(
        width="100%", thickness=0.5, color=colors.HexColor("#cccccc"),
    ))
    content.append(Paragraph(
        f"Documento generado automáticamente por GIAbot el {fecha_formato}.",
        estilo_meta,
    ))

    doc.build(content)
    return str(ruta)
