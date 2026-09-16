import os
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

app = FastAPI()

# Ваш Google Client ID
GOOGLE_CLIENT_ID = "456399427995-vm7t0rmj7ar701lhj3v5i72pgjcm1tep.apps.googleusercontent.com"

# Хранилища в памяти
chat_histories = {}
user_db = {}  # token -> user_info

class ChatRequest(BaseModel):
    prompt: str
    session_id: str

class GoogleLoginRequest(BaseModel):
    credential: str

@app.post("/api/google-login")
def google_login(data: GoogleLoginRequest):
    try:
        idinfo = id_token.verify_oauth2_token(
            data.credential, 
            google_requests.Request(), 
            GOOGLE_CLIENT_ID
        )

        user_email = idinfo.get("email")
        user_name = idinfo.get("name", user_email)
        user_picture = idinfo.get("picture", "")  # Аватарка из профиля Google
        
        access_token = f"token_{user_email}"
        user_db[access_token] = {
            "email": user_email,
            "username": user_name,
            "picture": user_picture
        }

        return {"access_token": access_token, "username": user_name, "picture": user_picture}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e) or "Неверный Google Token")

@app.get("/api/me")
def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        return {"username": None, "picture": ""}
    
    token = authorization.split(" ")[1]
    user = user_db.get(token)
    if not user:
        return {"username": None, "picture": ""}
    
    return {
        "username": user["username"], 
        "email": user["email"],
        "picture": user.get("picture", "")
    }

@app.get("/api/history")
def get_history(session_id: str, authorization: Optional[str] = Header(None)):
    history = chat_histories.get(session_id, [])
    # Возвращаем ключи и для истории, и для сообщений, чтобы фронтенд корректно отрисовал чат
    return {"history": history, "messages": history}

@app.post("/api/chat")
def chat_endpoint(data: ChatRequest, authorization: Optional[str] = Header(None)):
    if data.session_id not in chat_histories:
        chat_histories[data.session_id] = []

    # Добавляем сообщение пользователя
    chat_histories[data.session_id].append({
        "role": "user",
        "content": data.prompt
    })

    # Обработка ответа
    if data.prompt.lower().startswith("нарисуй:"):
        image_prompt = data.prompt[7:].strip()
        bot_response = f"Сгенерировано изображение по запросу: «{image_prompt}»"
        image_url = "https://picsum.photos/800/600"
    else:
        bot_response = f"Rubinov AI получил ваше сообщение: «{data.prompt}». Всё работает отлично!"
        image_url = None

    # Добавляем ответ бота
    chat_histories[data.session_id].append({
        "role": "bot",
        "content": bot_response,
        "response": bot_response,
        "image_url": image_url
    })

    return {
        "response": bot_response,
        "image_url": image_url
    }

# Подключаем статические файлы интерфейса из папки public
app.mount("/", StaticFiles(directory="public", html=True), name="public")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
