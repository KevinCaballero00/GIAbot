from google import genai
from google.genai import types
import os
from dotenv import load_dotenv
from models.knowledge import GIA_INFO
from services.scraper import CONTEXTO_WEB

load_dotenv()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

SYSTEM_PROMPT = GIA_INFO + f"""

=====================
INFORMACIÓN ACTUALIZADA DE LA PÁGINA WEB DEL GIA
=====================
La siguiente información fue extraída directamente del sitio web oficial del grupo GIA.
Úsala para responder preguntas sobre investigadores, semilleros, proyectos y servicios.

Notas sobre el formato:
- Los enlaces académicos aparecen en línea como [texto — Tipo: URL]
  (por ejemplo, [Ver perfil — Google Scholar: https://scholar.google.com/...]).
- Hay un bloque titulado "Directorio estructurado de docentes / investigadores
  del GIA" que agrupa, por persona, todos sus perfiles verificados
  (Google Scholar, ORCID, ResearchGate, CvLAC, etc.). Cuando un usuario
  pregunte por un investigador específico, cita esos enlaces directamente y
  preséntalos como una lista breve en vez de inventar URLs.
- Si un enlace no aparece en el contexto, dilo explícitamente en lugar de
  fabricarlo.

{CONTEXTO_WEB}
"""

def generar_respuesta(message, history):
    history_formatted = [
        types.Content(
            role="model" if h["role"] == "assistant" else "user",
            parts=[types.Part(text=h["content"])]
        )
        for h in history
    ]

    response = client.models.generate_content(
        model="models/gemini-2.0-flash",
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        contents=history_formatted + [
            types.Content(role="user", parts=[types.Part(text=message)])
        ]
    )

    return response.text
