from pydantic import BaseModel

class Message(BaseModel):
    message: str
    history: list = []
    session_id: str = "default"