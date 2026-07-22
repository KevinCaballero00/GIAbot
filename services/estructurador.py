"""
Estructurador de proyectos con Gemini.

El scraping del CvLAC y de la página del GIA devuelve texto crudo y desordenado.
Este módulo usa el LLM (Gemini) para convertir ese texto en una lista limpia de
líneas de investigación con los campos exactos que necesita el FO-IN-17:

    linea, proyecto, objetivo, actividades (lista), responsable, producto

Regla de oro: NO inventar datos. Si un campo no aparece en el texto, queda vacío.
El número de líneas se limita a `max_lineas` (5-6) porque el formato oficial del
GIA contempla a lo sumo ese número de líneas de investigación.
"""
from __future__ import annotations

import difflib
import json
import logging
import unicodedata
from collections import Counter

from google.genai import types

from services.ai_service import client
from services.validadores_fo_in_17 import parse_fecha, parse_nivel

logger = logging.getLogger(__name__)

MODELO = "models/gemini-2.5-flash"

# Líneas de investigación oficiales del GIA (gia.ufps.edu.co/about/). Toda
# línea libre que devuelva el LLM se mapea a una de estas 4 en post-proceso.
LINEAS_OFICIALES_GIA = [
    "Sistemas Inteligentes Aplicados",
    "Desarrollo de Sistemas Inteligentes",
    "Tópicos Emergentes",
    "Transformación Digital",
]
_LINEA_DEFAULT = LINEAS_OFICIALES_GIA[0]


def _normalizar_texto(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", (texto or "").strip().lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_LINEAS_NORMALIZADAS = {_normalizar_texto(linea): linea for linea in LINEAS_OFICIALES_GIA}


def _mapear_linea_oficial(texto: str) -> str | None:
    """Mapea una línea de investigación en texto libre (la que devuelve el LLM)
    a la línea oficial del GIA más cercana. Devuelve None si el texto viene
    vacío o no hay ninguna coincidencia razonable (el llamador decide el
    default, típicamente la línea más representada en el propio resultado)."""
    normalizado = _normalizar_texto(texto)
    if not normalizado:
        return None

    if normalizado in _LINEAS_NORMALIZADAS:
        return _LINEAS_NORMALIZADAS[normalizado]

    coincidencias = difflib.get_close_matches(
        normalizado, _LINEAS_NORMALIZADAS.keys(), n=1, cutoff=0.4
    )
    if coincidencias:
        return _LINEAS_NORMALIZADAS[coincidencias[0]]

    # Respaldo por palabra clave para frases que difflib no acerca lo suficiente
    for norm, oficial in _LINEAS_NORMALIZADAS.items():
        palabras_clave = [p for p in norm.split() if len(p) > 4]
        if any(p in normalizado for p in palabras_clave):
            return oficial

    return None

_PROMPT = """Eres un asistente que estructura información de investigación académica
para el formato oficial FO-IN-17 (Plan de Acción de Grupos de Investigación) de la
Universidad Francisco de Paula Santander.

A continuación recibirás TEXTO CRUDO extraído de perfiles CvLAC de varios docentes
del grupo GIA. El texto trae los proyectos agrupados en bloques, cada uno precedido
por una línea "Docente: <nombre>" que indica a quién pertenece ese bloque. Tu tarea
es identificar los proyectos de investigación MÁS RECIENTES del grupo (no de un solo
docente) y devolverlos como JSON limpio.

Periodo académico actual: "{periodo_actual}"

Devuelve EXCLUSIVAMENTE un arreglo JSON (sin texto adicional) con MÁXIMO {max_objetos}
objetos. Cada objeto debe tener exactamente estas claves:
  - "linea": EXACTAMENTE una de estas 4 líneas oficiales del GIA (elige la que mejor
    represente el proyecto; no inventes ni uses otro nombre):
{lineas_oficiales}
  - "proyecto": título del proyecto a ejecutar (conciso, sin el resumen completo).
  - "objetivo": objetivo principal del proyecto (1-2 frases).
  - "actividades": lista de strings con las actividades principales (máx. 5 ítems cortos).
    Si las actividades no aparecen explícitas en el texto, DEDÚCELAS lógicamente del
    objetivo y descripción del proyecto (p. ej. "Revisión de literatura", "Diseño
    metodológico", "Recolección y análisis de datos", "Redacción de artículo"). No
    dejes este campo como lista vacía si hay información del proyecto disponible.
  - "responsable": nombre EXACTO del docente indicado en el "Docente:" del bloque de
    origen de ese proyecto. No inventes ni asumas un responsable distinto al del bloque.
  - "producto": producto esperado (ponencia, artículo, software, prototipo, etc.).
    Si no aparece explícitamente, infiere el producto académico más probable según
    el tipo de proyecto. No dejes vacío si hay descripción del proyecto.
  - "periodo": el rango o año del proyecto tal como aparece en el texto (ej. "2025 -",
    "2024 - 2026"). Cadena vacía "" si no hay fecha visible.

Selección de proyectos:
  - Prioriza los proyectos más recientes: cuyo rango de fechas incluya el año de
    "{periodo_actual}" o esté abierto (sin fecha de fin).
  - Si no hay suficientes proyectos vigentes para llenar los {max_lineas} cupos,
    completa con los del año inmediatamente anterior.
  - Ordena el resultado del proyecto más reciente al más antiguo.
  - Diversidad de docentes: si hay más candidatos que cupos, no incluyas más de 2
    proyectos del mismo docente para que el plan grupal represente a varios docentes.
  - Diversidad de líneas: si el texto respalda proyectos de más de una de las 4 líneas
    oficiales, procura que el resultado final cubra varias líneas en vez de concentrarse
    en una sola. Nunca fuerces una línea que no tenga proyectos reales que la respalden.

Reglas estrictas:
  - NO inventes títulos de proyecto, objetivos ni nombre de responsable; si no
    aparecen en el texto déjalos como cadena vacía "".
  - Para "actividades" y "producto" sí puedes deducir a partir del objetivo/descripción.
  - Agrupa información que pertenezca al mismo proyecto; no dupliques líneas.
  - Si no hay proyectos identificables, devuelve un arreglo vacío [].

TEXTO CRUDO:
---
{texto}
---
"""


# Margen de candidatos crudos por encima de `max_lineas` para poder aplicar
# selección con diversidad de líneas en Python sin perder cobertura.
_CANDIDATOS_EXTRA = 2


def _seleccionar_con_diversidad(candidatos: list[dict], max_lineas: int) -> list[dict]:
    """Recorta `candidatos` (ya ordenados por recencia por el LLM) a `max_lineas`,
    priorizando cubrir líneas oficiales distintas antes de repetir una línea."""
    if len(candidatos) <= max_lineas:
        return candidatos

    seleccionados: list[dict] = []
    ids_seleccionados: set[int] = set()
    lineas_usadas: set[str] = set()

    for p in candidatos:
        if len(seleccionados) >= max_lineas:
            break
        if p["linea"] not in lineas_usadas:
            seleccionados.append(p)
            ids_seleccionados.add(id(p))
            lineas_usadas.add(p["linea"])

    if len(seleccionados) < max_lineas:
        restantes = [p for p in candidatos if id(p) not in ids_seleccionados]
        seleccionados.extend(restantes[: max_lineas - len(seleccionados)])

    return seleccionados[:max_lineas]


def estructurar_proyectos(
    texto_crudo: str,
    periodo_actual: str,
    max_lineas: int = 6,
) -> list[dict]:
    """
    Estructura el texto crudo (multi-docente) en una lista grupal de líneas de
    investigación limpias.

    Retorna una lista de dicts con claves: linea, proyecto, objetivo,
    actividades (list[str]), responsable, producto, periodo. Devuelve [] si Gemini
    falla o no encuentra proyectos (el llamador decide el fallback).
    """
    texto = (texto_crudo or "").strip()
    if not texto:
        return []

    # Acotar el texto para no exceder límites del modelo
    texto = texto[:40000]

    max_objetos = max_lineas + _CANDIDATOS_EXTRA
    lineas_oficiales = "\n".join(
        f"      {i}. {linea}" for i, linea in enumerate(LINEAS_OFICIALES_GIA, start=1)
    )

    prompt = _PROMPT.format(
        periodo_actual=periodo_actual or "",
        max_lineas=max_lineas,
        max_objetos=max_objetos,
        lineas_oficiales=lineas_oficiales,
        texto=texto,
    )

    try:
        response = client.models.generate_content(
            model=MODELO,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )
        data = json.loads(response.text)
    except Exception as exc:
        logger.warning("Estructurador: fallo al estructurar con Gemini – %s", exc)
        return []

    if not isinstance(data, list):
        logger.warning("Estructurador: respuesta no es lista (%s)", type(data).__name__)
        return []

    candidatos: list[dict] = []
    for item in data[:max_objetos]:
        if not isinstance(item, dict):
            continue
        actividades = item.get("actividades") or []
        if isinstance(actividades, str):
            actividades = [actividades]
        candidatos.append({
            "linea": str(item.get("linea") or "").strip(),
            "proyecto": str(item.get("proyecto") or "").strip(),
            "objetivo": str(item.get("objetivo") or "").strip(),
            "actividades": [str(a).strip() for a in actividades if str(a).strip()],
            "responsable": str(item.get("responsable") or "").strip(),
            "producto": str(item.get("producto") or "").strip(),
            "periodo": str(item.get("periodo") or "").strip(),
        })

    # Mapear cada línea libre del modelo a una de las 4 líneas oficiales del GIA
    for p in candidatos:
        oficial = _mapear_linea_oficial(p["linea"])
        if oficial:
            p["linea"] = oficial

    conteo_lineas = Counter(
        p["linea"] for p in candidatos if p["linea"] in LINEAS_OFICIALES_GIA
    )
    linea_default = conteo_lineas.most_common(1)[0][0] if conteo_lineas else _LINEA_DEFAULT
    for p in candidatos:
        if p["linea"] not in LINEAS_OFICIALES_GIA:
            p["linea"] = linea_default

    proyectos = _seleccionar_con_diversidad(candidatos, max_lineas)

    logger.info("Estructurador: %d líneas estructuradas (grupal, periodo '%s')", len(proyectos), periodo_actual)
    return proyectos


_PROMPT_TRABAJOS = """Eres un asistente que extrae información de TRABAJOS DE GRADO DIRIGIDOS
(tesis, trabajos de pregrado/especialización/maestría/doctorado, tutorías) desde perfiles
CvLAC de docentes del grupo de investigación GIA, para el formato oficial FO-IN-17,
sección "2. Participación en Dirección de".

Periodo académico actual: "{periodo_actual}"

A continuación recibirás TEXTO CRUDO de las secciones "Trabajos dirigidos" / "Tutorías" /
"Tesis" de varios docentes. Cada bloque puede venir precedido por una línea
"Docente: <nombre>" que indica quién dirige ese trabajo.

Devuelve EXCLUSIVAMENTE un arreglo JSON (sin texto adicional) con MÁXIMO {max_filas}
objetos. Cada objeto debe tener exactamente estas claves:
  - "titulo": título del trabajo de grado.
  - "estudiante": nombre del estudiante dirigido.
  - "director": nombre del docente director (el "Docente:" del bloque de origen).
  - "programa": programa académico del estudiante, si aparece explícito.
  - "institucion": institución del programa, si aparece explícita; usa "Universidad
    Francisco de Paula Santander" si el contexto no indica otra institución.
  - "nivel": uno de "Pregrado", "Especialización", "Maestría" o "Doctorado" según el
    tipo de trabajo. Si no se puede determinar con certeza, usa cadena vacía "".

Selección:
  - Prioriza los trabajos más recientes: cuyo año coincida con "{periodo_actual}" o
    esté abierto (sin fecha de fin).
  - No incluyas más de {max_filas} trabajos.
  - Ordena el resultado del más reciente al más antiguo.

Reglas estrictas:
  - NO inventes títulos, nombres de estudiantes ni directores; si un dato no aparece
    explícitamente en el texto, déjalo como cadena vacía "".
  - No dupliques el mismo trabajo.
  - Si no hay trabajos identificables, devuelve un arreglo vacío [].

TEXTO CRUDO:
---
{texto}
---
"""

_NIVELES_TRABAJO_VALIDOS = {"Pregrado", "Especialización", "Maestría", "Doctorado"}


def estructurar_trabajos_grado(
    texto_crudo: str,
    periodo_actual: str,
    max_filas: int = 6,
) -> list[dict]:
    """
    Estructura el texto crudo de "trabajos dirigidos" (CvLAC) en una lista de
    sugerencias para la sección 2 del FO-IN-17.

    Sin fallback crudo: si Gemini falla o no encuentra nada, retorna lista
    vacía y el flujo conversacional del chat pregunta desde cero. Estas
    sugerencias nunca se persisten directamente en el PDF sin que el docente
    las confirme por chat.
    """
    texto = (texto_crudo or "").strip()
    if not texto:
        return []

    texto = texto[:40000]

    prompt = _PROMPT_TRABAJOS.format(
        periodo_actual=periodo_actual or "",
        max_filas=max_filas,
        texto=texto,
    )

    try:
        response = client.models.generate_content(
            model=MODELO,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )
        data = json.loads(response.text)
    except Exception as exc:
        logger.warning("Estructurador: fallo al estructurar trabajos de grado – %s", exc)
        return []

    if not isinstance(data, list):
        logger.warning("Estructurador: respuesta de trabajos no es lista (%s)", type(data).__name__)
        return []

    trabajos: list[dict] = []
    for item in data[:max_filas]:
        if not isinstance(item, dict):
            continue
        nivel = str(item.get("nivel") or "").strip()
        if nivel not in _NIVELES_TRABAJO_VALIDOS:
            nivel = ""
        trabajos.append({
            "titulo": str(item.get("titulo") or "").strip(),
            "estudiante": str(item.get("estudiante") or "").strip(),
            "director": str(item.get("director") or "").strip(),
            "programa": str(item.get("programa") or "").strip(),
            "institucion": str(item.get("institucion") or "").strip(),
            "nivel": nivel,
        })

    logger.info(
        "Estructurador: %d trabajos de grado sugeridos (periodo '%s')",
        len(trabajos), periodo_actual,
    )
    return trabajos


_PROMPT_SECCIONES = """Eres un asistente que estructura información YA REDACTADA por un
docente del grupo de investigación GIA, para completar las secciones 2, 3 y 4 del formato
oficial FO-IN-17 (Plan de Acción de Grupos de Investigación) de la Universidad Francisco de
Paula Santander.

A continuación recibirás TEXTO CRUDO extraído de un documento (.txt/.docx/.pdf) que el
docente subió con información ya organizada sobre trabajos de grado dirigidos, eventos de
investigación/científicos y otras actividades del grupo. Tu tarea es ESTRUCTURAR ese texto,
no redactarlo desde cero ni completar huecos con suposiciones.

Periodo académico actual: "{periodo_actual}"

Devuelve EXCLUSIVAMENTE un objeto JSON (sin texto adicional) con esta forma exacta:
{{
  "trabajos_grado": [
    {{"titulo": "", "estudiante": "", "director": "", "programa": "", "institucion": "", "nivel": ""}}
  ],
  "eventos": [
    {{"nombre": "", "fecha": "", "responsable": "", "institucion_promotora": "", "entidades_participantes": ""}}
  ],
  "fechas_otras_actividades": {{
    "coordinacion_semillero": "", "eventos_academicos": "", "actualizaciones": "", "reunion_mensual": ""
  }}
}}

Reglas:
  - MÁXIMO 6 objetos en "trabajos_grado" y MÁXIMO 4 en "eventos" (son los topes del formato).
  - "nivel" debe ser exactamente uno de: "Pregrado", "Especialización", "Maestría" o
    "Doctorado". Si no se puede determinar con certeza, deja cadena vacía "".
  - Las fechas ("fecha" de cada evento y las 4 de "fechas_otras_actividades"): cópialas tal
    como aparecen en el texto, sin reformatearlas (el llamador las normaliza). Cadena vacía
    si no aparecen explícitamente.
  - "institucion" (trabajos) e "institucion_promotora" (eventos): solo si el texto la
    menciona explícitamente; en otro caso deja cadena vacía "" (el llamador aplica el
    default institucional).
  - "responsable" (eventos): solo si el texto lo menciona explícitamente; en otro caso deja
    cadena vacía "" (el llamador aplica el default).
  - NO inventes títulos, nombres de estudiantes, directores ni nombres de eventos; si un
    dato no aparece explícitamente en el texto, déjalo como cadena vacía "".
  - Si una sección no tiene información en el texto, devuélvela vacía (arreglo [] o valores
    "" en el objeto de fechas), nunca inventada.

TEXTO CRUDO:
---
{texto}
---
"""


def estructurar_secciones_desde_texto(texto: str, periodo_actual: str) -> dict:
    """
    Estructura un documento subido por el docente (ya redactado, no CvLAC) en el
    mismo shape que consume `fo_in_17_service.actualizar_datos_recolectados`:
    trabajos_grado, eventos, fechas_otras_actividades.

    Regla de oro: NO inventar. Devuelve secciones vacías si Gemini falla o no
    encuentra nada; el llamador (endpoint de subida) decide si mostrar el resumen
    vacío o sugerir volver al flujo paso a paso.
    """
    vacio = {"trabajos_grado": [], "eventos": [], "fechas_otras_actividades": {}}
    texto = (texto or "").strip()
    if not texto:
        return vacio

    texto = texto[:40000]
    prompt = _PROMPT_SECCIONES.format(periodo_actual=periodo_actual or "", texto=texto)

    try:
        response = client.models.generate_content(
            model=MODELO,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
            contents=[types.Content(role="user", parts=[types.Part(text=prompt)])],
        )
        data = json.loads(response.text)
    except Exception as exc:
        logger.warning("Estructurador: fallo al estructurar secciones desde documento – %s", exc)
        return vacio

    if not isinstance(data, dict):
        logger.warning(
            "Estructurador: respuesta de secciones no es objeto (%s)", type(data).__name__
        )
        return vacio

    trabajos: list[dict] = []
    for item in (data.get("trabajos_grado") or [])[:6]:
        if not isinstance(item, dict):
            continue
        nivel = parse_nivel(str(item.get("nivel") or "")) or ""
        trabajos.append({
            "titulo": str(item.get("titulo") or "").strip(),
            "estudiante": str(item.get("estudiante") or "").strip(),
            "director": str(item.get("director") or "").strip(),
            "programa": str(item.get("programa") or "").strip(),
            "institucion": (
                str(item.get("institucion") or "").strip()
                or "Universidad Francisco de Paula Santander"
            ),
            "nivel": nivel,
        })

    eventos: list[dict] = []
    for item in (data.get("eventos") or [])[:4]:
        if not isinstance(item, dict):
            continue
        fecha_raw = str(item.get("fecha") or "").strip()
        eventos.append({
            "nombre": str(item.get("nombre") or "").strip(),
            "fecha": parse_fecha(fecha_raw) or fecha_raw,
            "responsable": str(item.get("responsable") or "").strip() or "Miembros GIA",
            "institucion_promotora": (
                str(item.get("institucion_promotora") or "").strip()
                or "Universidad Francisco de Paula Santander"
            ),
            "entidades_participantes": str(item.get("entidades_participantes") or "").strip(),
        })

    fechas_raw = data.get("fechas_otras_actividades") or {}
    fechas_otras: dict[str, str] = {}
    for clave in (
        "coordinacion_semillero", "eventos_academicos", "actualizaciones", "reunion_mensual",
    ):
        valor = str(fechas_raw.get(clave) or "").strip()
        fechas_otras[clave] = (parse_fecha(valor) or valor) if valor else ""

    logger.info(
        "Estructurador: documento -> %d trabajos, %d eventos, %d fechas (periodo '%s')",
        len(trabajos), len(eventos), len(fechas_otras), periodo_actual,
    )
    return {
        "trabajos_grado": trabajos,
        "eventos": eventos,
        "fechas_otras_actividades": fechas_otras,
    }
