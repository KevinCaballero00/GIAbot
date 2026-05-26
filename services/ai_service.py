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
La siguiente información fue extraída directamente del sitio web oficial del
grupo GIA, incluyendo el contenido de los perfiles académicos de cada docente
(CvLAC, Google Scholar, ORCID, ResearchGate).

Instrucciones para usar esta información:
- En el bloque "Directorio de docentes / investigadores del GIA" encontrarás,
  para cada investigador: sus enlaces académicos y el contenido real extraído
  de esos perfiles (títulos académicos, publicaciones, etc.).
- Cuando el usuario pregunte por los títulos académicos o formación de un
  docente, busca la sección "Formación académica" dentro de su perfil.
- Cuando pregunten por publicaciones o artículos, usa la lista de publicaciones
  extraída de Google Scholar, ORCID o CvLAC según esté disponible.
- Responde con los datos concretos del contexto, no con los links. Solo incluye
  los links si el usuario los pide explícitamente o como referencia adicional.
- Si para un docente no se pudo obtener información de sus perfiles, indícalo
  y sugiere visitar directamente sus enlaces.
- No inventes títulos, publicaciones ni datos académicos.

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
