import os
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "456399427995-vm7t0rmj7ar701lhj3v5i72pgjcm1tep.apps.googleusercontent.com")

class ChatRequest(BaseModel):
    message: str

def verify_google_token(token: str):
    # Добавьте проверку google-auth JWT при необходимости
    return {"email": "user@gmail.com"} if token else None

@app.post("/api/chat")
async def chat_endpoint(data: ChatRequest, authorization: Optional[str] = Header(None)):
    is_authenticated = False
    
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ")[1]
        user = verify_google_token(token)
        if user:
            is_authenticated = True

    # Серверный ответ
    response_text = f"Эхо: {data.message}. (Авторизован: {is_authenticated})"
    return {"reply": response_text, "authenticated": is_authenticated}

app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
