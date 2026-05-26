import os
import tempfile
import shutil
from datetime import datetime
from typing import Dict, List, Optional

# Opción 1: Si tus PDFs tienen campos de formulario (recomendado)
try:
    from pypdf import PdfReader, PdfWriter
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False
    print("⚠️ pypdf no instalado. Ejecuta: pip install pypdf")

# Opción 2: Si no tienen campos (usar reportlab overlay)
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
import io


class PDFCompleter:
    """Clase para manejar el llenado de formularios PDF del GIA"""
    
    # Mapeo de tipos de PDF a sus archivos originales y campos
    PDF_CONFIG = {
        13: {
            "nombre": "FO-IN-13",
            "descripcion": "Informe de Gestión de Grupos de Investigación",
            "ruta_original": "static/docs/FO-IN-13 INFORME GESTION GRUPOS INV V1.pdf",
            "campos": [
                "nombre_grupo", "fecha", "periodo", "integrante1", "integrante2",
                "proyecto1", "proyecto2", "logros", "publicaciones"
            ]
        },
        17: {
            "nombre": "FO-IN-17",
            "descripcion": "Plan de Acción de Grupos de Investigación",
            "ruta_original": "static/docs/FO-IN-17 PLAN DE ACCION GRUPOS INV V1.pdf",
            "campos": [
                "nombre_grupo", "fecha", "semestre", "objetivo1", "objetivo2",
                "actividad1", "actividad2", "responsable", "fecha_inicio", "fecha_fin"
            ]
        }
    }
    
    def __init__(self, output_dir: str = "files"):
        """Inicializa el completador con un directorio de salida"""
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
    
    def listar_campos_pdf(self, pdf_numero: int) -> Optional[Dict]:
        """
        Lista los campos disponibles en el PDF (si tiene AcroForm)
        Retorna dict con los nombres de los campos
        """
        if not HAS_PYPDF:
            return None
            
        config = self.PDF_CONFIG.get(pdf_numero)
        if not config:
            return None
            
        try:
            reader = PdfReader(config["ruta_original"])
            fields = reader.get_fields()
            if fields:
                return {"campos_encontrados": list(fields.keys())}
            else:
                return {"mensaje": "El PDF no tiene campos de formulario editables"}
        except Exception as e:
            return {"error": str(e)}
    
    def completar_pdf(
        self, 
        pdf_numero: int, 
        datos: Dict[str, str],
        output_filename: Optional[str] = None
    ) -> str:
        """
        Completa el PDF con los datos proporcionados
        
        Args:
            pdf_numero: 13 o 17
            datos: Diccionario con los campos a llenar
            output_filename: Nombre del archivo de salida (opcional)
        
        Returns:
            Ruta del archivo PDF modificado
        """
        config = self.PDF_CONFIG.get(pdf_numero)
        if not config:
            raise ValueError(f"PDF número {pdf_numero} no soportado")
        
        # Verificar que existe el PDF original
        if not os.path.exists(config["ruta_original"]):
            raise FileNotFoundError(f"No se encontró: {config['ruta_original']}")
        
        # Generar nombre de salida si no se proporcionó
        if not output_filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_filename = f"{config['nombre']}_completado_{timestamp}.pdf"
        
        output_path = os.path.join(self.output_dir, output_filename)
        
        # Copiar el archivo original como base
        shutil.copy2(config["ruta_original"], output_path)
        
        # Intentar llenar formulario (si tiene campos)
        if HAS_PYPDF:
            try:
                return self._llenar_con_pypdf(output_path, datos)
            except Exception as e:
                print(f"Error con pypdf, usando fallback: {e}")
                return self._llenar_con_overlay(output_path, datos, config["ruta_original"])
        else:
            return self._llenar_con_overlay(output_path, datos, config["ruta_original"])
    
    def _llenar_con_pypdf(self, pdf_path: str, datos: Dict[str, str]) -> str:
        """Método principal: rellena campos AcroForm si existen"""
        reader = PdfReader(pdf_path)
        writer = PdfWriter()
        
        # Obtener campos existentes
        campos_disponibles = reader.get_fields()
        
        if campos_disponibles:
            # Filtrar solo los campos que existen en el PDF
            datos_filtrados = {
                k: v for k, v in datos.items() 
                if k in campos_disponibles
            }
            writer.append(reader)
            writer.update_page_form_field_values(writer.pages[0], datos_filtrados)
        else:
            # Fallback: usar el método de overlay
            return self._llenar_con_overlay(pdf_path, datos, pdf_path)
        
        with open(pdf_path, "wb") as f:
            writer.write(f)
        
        return pdf_path
    
    def _llenar_con_overlay(self, output_path: str, datos: Dict[str, str], original_path: str) -> str:
        """Método alternativo: dibuja texto en coordenadas fijas sobre el PDF"""
        reader = PdfReader(original_path)
        writer = PdfWriter()
        
        # Definir coordenadas para cada campo (AJUSTA ESTOS VALORES SEGÚN TU PDF)
        COORDENADAS = {
            "nombre_grupo": (100, 750),
            "fecha": (400, 750),
            "periodo": (100, 720),
            "integrante1": (100, 690),
            "integrante2": (100, 660),
            "proyecto1": (100, 630),
            "proyecto2": (100, 600),
            "logros": (100, 550),
            "publicaciones": (100, 500),
            "semestre": (300, 750),
            "objetivo1": (100, 700),
            "objetivo2": (100, 660),
            "actividad1": (100, 620),
            "actividad2": (100, 580),
            "responsable": (100, 540),
            "fecha_inicio": (300, 540),
            "fecha_fin": (500, 540),
        }
        
        # Crear overlay con ReportLab
        packet = io.BytesIO()
        
        # Obtener dimensiones de la página original
        primera_pagina = reader.pages[0]
        page_width = float(primera_pagina.mediabox.width)
        page_height = float(primera_pagina.mediabox.height)
        
        c = canvas.Canvas(packet, pagesize=(page_width, page_height))
        c.setFont("Helvetica", 10)
        
        for key, value in datos.items():
            if key in COORDENADAS:
                x, y = COORDENADAS[key]
                c.drawString(x, y, str(value))
        
        c.save()
        packet.seek(0)
        overlay = PdfReader(packet)
        
        # Fusionar overlay con cada página
        for i in range(len(reader.pages)):
            page = reader.pages[i]
            if i < len(overlay.pages):
                page.merge_page(overlay.pages[i])
            writer.add_page(page)
        
        with open(output_path, "wb") as f:
            writer.write(f)
        
        return output_path


# Instancia global
pdf_completer = PDFCompleter()