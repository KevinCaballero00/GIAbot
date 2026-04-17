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
        model="models/gemini-2.5-flash",
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
        contents=history_formatted + [
            types.Content(role="user", parts=[types.Part(text=message)])
        ]
    )

    return response.text
