from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

def generar_pdf(data: dict, filename="documento.pdf"):
    doc = SimpleDocTemplate(filename)
    styles = getSampleStyleSheet()

    content = []

    # Título
    content.append(Paragraph("Documento Generado por GIAbot", styles['Title']))
    content.append(Spacer(1, 12))

    # Contenido dinámico
    for key, value in data.items():
        texto = f"<b>{key}:</b> {value}"
        content.append(Paragraph(texto, styles['Normal']))
        content.append(Spacer(1, 10))

    doc.build(content)

    return filename
