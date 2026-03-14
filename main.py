from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types
import os
from dotenv import load_dotenv
from knowledge import GIA_INFO

load_dotenv()

client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
    message: str
    history: list = []

@app.post("/chat")
async def chat(data: Message):
    history = [
        types.Content(role=h["role"], parts=[types.Part(text=h["content"])])
        for h in data.history
    ]
    
    response = client.models.generate_content(
        model="models/gemini-2.5-flash",
        config=types.GenerateContentConfig(system_instruction=GIA_INFO),
        contents=history + [types.Content(role="user", parts=[types.Part(text=data.message)])]
    )
    
    return {"reply": response.text}
