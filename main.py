import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from jose import JWTError, jwt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================

PORT = int(os.getenv("PORT", 3000))
SECRET_KEY = os.getenv("JWT_SECRET", "rubinov_ai_secure_jwt_token_key_2026_984f1a27b8c0e42d")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней
DB_NAME = "database.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            google_id TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            picture TEXT,
            is_vip INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==========================================
# FASTAPI И JWT
# ==========================================

app = FastAPI(title="Rubinov AI Web API")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/google")

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Сессия истекла или токен недействителен",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        google_id: str = payload.get("sub")
        if google_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT google_id, email, name, picture, is_vip, is_banned FROM users WHERE google_id = ?", (google_id,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise credentials_exception
    if user[5] == 1:
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован")

    return {
        "google_id": user[0],
        "email": user[1],
        "name": user[2],
        "picture": user[3],
        "is_vip": bool(user[4])
    }

# ==========================================
# API АВТОРИЗАЦИИ ЧЕРЕЗ GOOGLE И ЧАТ
# ==========================================

class GoogleAuthRequest(BaseModel):
    credential: str

@app.post("/api/auth/google")
def google_auth(data: GoogleAuthRequest):
    try:
        id_info = id_token.verify_oauth2_token(
            data.credential, 
            google_requests.Request(), 
            GOOGLE_CLIENT_ID
        )

        google_id = id_info['sub']
        email = id_info.get('email', '')
        name = id_info.get('name', '')
        picture = id_info.get('picture', '')

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute("SELECT google_id FROM users WHERE google_id = ?", (google_id,))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute(
                "INSERT INTO users (google_id, email, name, picture, created_at) VALUES (?, ?, ?, ?, ?)",
                (google_id, email, name, picture, datetime.utcnow().isoformat())
            )
        else:
            cursor.execute(
                "UPDATE users SET email = ?, name = ?, picture = ? WHERE google_id = ?",
                (email, name, picture, google_id)
            )

        conn.commit()
        conn.close()

        token = create_access_token(
            data={"sub": google_id},
            expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        )

        return {"access_token": token, "token_type": "bearer"}

    except ValueError:
        raise HTTPException(status_code=400, detail="Невалидный токен Google")

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return current_user

class ChatRequest(BaseModel):
    message: str

@app.post("/api/chat")
def chat_endpoint(data: ChatRequest, current_user: dict = Depends(get_current_user)):
    msg = data.message.lower()
    
    if "нарисуй" in msg or "сгенерируй" in msg or "картинк" in msg:
        return {
            "reply": f"Вот сгенерированное изображение по запросу: '{data.message}'",
            "image_url": "https://picsum.photos/600/400"
        }
        
    vip_prefix = "[VIP] " if current_user["is_vip"] else ""
    return {
        "reply": f"{vip_prefix}Привет, {current_user['name']}! Запрос '{data.message}' обработан."
    }

# ==========================================
# СТАТИКА И ФРОНТЕНД
# ==========================================

if os.path.exists("public"):
    app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    file_path = os.path.join("public", full_path)
    if os.path.exists(file_path) and os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse("public/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
