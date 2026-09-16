import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

SECRET_KEY = "rubinov_ai_secret_key_change_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 часа
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token", auto_error=False)

app = FastAPI(title="Rubinov AI Server")
DATABASE = "rubinov_ai.db"

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT,
            google_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_id TEXT,
            role TEXT NOT NULL,
            content TEXT,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class UserRegister(BaseModel):
    username: str
    password: str

class GoogleAuthRequest(BaseModel):
    credential: str

class ChatRequest(BaseModel):
    prompt: str
    session_id: str
    image: Optional[str] = None

def get_current_user_optional(token: Optional[str] = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
    except JWTError:
        return None

    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(user) if user else None

@app.get("/", response_class=HTMLResponse)
def serve_index():
    if os.path.exists("public/index.html"):
        with open("public/index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h3>index.html not found in public folder</h3>"

if os.path.exists("public"):
    app.mount("/public", StaticFiles(directory="public"), name="public")

@app.post("/api/register")
def register(user_data: UserRegister, db: sqlite3.Connection = Depends(get_db)):
    username = user_data.username.strip()
    password = user_data.password.strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="Логин и пароль обязательны")
    
    hashed_pw = pwd_context.hash(password)
    try:
        db.execute("INSERT INTO users (username, hashed_password) VALUES (?, ?)", (username, hashed_pw))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    
    access_token = jwt.encode({"sub": username, "exp": datetime.utcnow() + timedelta(days=1)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": access_token, "token_type": "bearer", "username": username}

@app.post("/api/token")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE username = ?", (form_data.username,)).fetchone()
    if not user or not user["hashed_password"] or not pwd_context.verify(form_data.password, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Неверный логин или пароль")
    
    access_token = jwt.encode({"sub": user["username"], "exp": datetime.utcnow() + timedelta(days=1)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": access_token, "token_type": "bearer", "username": user["username"]}

@app.post("/api/google-login")
def google_login(payload: GoogleAuthRequest, db: sqlite3.Connection = Depends(get_db)):
    try:
        idinfo = id_token.verify_oauth2_token(payload.credential, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo["email"]
        name = idinfo.get("name", email.split("@")[0])
    except Exception as e:
        raise HTTPException(status_code=400, detail="Ошибка авторизации Google")

    user = db.execute("SELECT * FROM users WHERE username = ?", (email,)).fetchone()
    if not user:
        db.execute("INSERT INTO users (username, google_id) VALUES (?, ?)", (email, idinfo["sub"]))
        db.commit()

    access_token = jwt.encode({"sub": email, "exp": datetime.utcnow() + timedelta(days=1)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": access_token, "token_type": "bearer", "username": email}

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user_optional)):
    if not current_user:
        return {"username": None}
    return {"username": current_user["username"]}

@app.post("/api/chat")
def chat_handler(payload: ChatRequest, request: Request, current_user: dict = Depends(get_current_user_optional), db: sqlite3.Connection = Depends(get_db)):
    prompt = payload.prompt.strip()
    session_id = payload.session_id
    if not prompt:
        raise HTTPException(status_code=400, detail="Пустой запрос")

    user_id = current_user["id"] if current_user else None

    # Проверка лимита для гостей (10 запросов по IP или session_id)
    if not current_user:
        count_row = db.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE user_id IS NULL and session_id = ? and role = 'user'", 
            (session_id,)
        ).fetchone()
        if count_row["cnt"] >= 10:
            raise HTTPException(
                status_code=403, 
                detail="Лимит бесплатного гостевого режима исчерпан (10 запросов). Войдите в аккаунт или через Google, чтобы продолжить без ограничений!"
            )

    db.execute("INSERT INTO messages (user_id, session_id, role, content) VALUES (?, ?, ?, ?)",
               (user_id, session_id, "user", prompt))
    db.commit()

    bot_response = f"Rubinov AI ответ: {prompt}"
    image_url = None
    if prompt.lower().startswith("нарисуй"):
        image_url = "https://picsum.photos/800/600"
        bot_response = "Сгенерированное изображение:"

    db.execute("INSERT INTO messages (user_id, session_id, role, content, image_url) VALUES (?, ?, ?, ?, ?)",
               (user_id, session_id, "bot", bot_response, image_url))
    db.commit()

    return {"response": bot_response, "image_url": image_url}

@app.get("/api/history")
def get_history(session_id: str, current_user: dict = Depends(get_current_user_optional), db: sqlite3.Connection = Depends(get_db)):
    if current_user:
        rows = db.execute("SELECT role, content, image_url FROM messages WHERE user_id = ? AND session_id = ? ORDER BY id ASC", (current_user["id"], session_id)).fetchall()
    else:
        rows = db.execute("SELECT role, content, image_url FROM messages WHERE user_id IS NULL AND session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
    return {"history": [dict(r) for r in rows]}
