import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt

# ==========================================
#  НАСТРОЙКИ И БЕЗОПАСНОСТЬ
# ==========================================
SECRET_KEY = "rubinov_ai_secret_key_change_in_production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 часа

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")

app = FastAPI(title="Rubinov AI Server")

DATABASE = "rubinov_ai.db"

# ==========================================
#  РАБОТА С БАЗОЙ ДАННЫХ SQLite
# ==========================================
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
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            hashed_password TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Таблица истории сообщений
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            image_url TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
#  PYDANTIC СХЕМЫ
# ==========================================
class UserRegister(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

class ChatRequest(BaseModel):
    prompt: str
    image: Optional[str] = None  # Base64 строка картинки

# ==========================================
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ АВТОРИЗАЦИИ
# ==========================================
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(token: str = Depends(oauth2_scheme), db: sqlite3.Connection = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if user is None:
        raise credentials_exception
    return dict(user)

# ==========================================
#  МАРШРУТЫ (ENDPOINTS)
# ==========================================

# 1. Регистрация
@app.post("/api/register")
def register(user_data: UserRegister, db: sqlite3.Connection = Depends(get_db)):
    username = user_data.username.strip()
    password = user_data.password.strip()

    if not username or not password:
        raise HTTPException(status_code=400, detail="Логин и пароль обязательны")

    hashed_pw = get_password_hash(password)
    try:
        cursor = db.cursor()
        cursor.execute("INSERT INTO users (username, hashed_password) VALUES (?, ?)", (username, hashed_pw))
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Пользователь с таким именем уже существует")

    return {"status": "ok", "message": "Регистрация успешна"}

# 2. Вход (Получение JWT-токена)
@app.post("/api/token", response_model=Token)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: sqlite3.Connection = Depends(get_db)):
    user = db.execute("SELECT * FROM users WHERE username = ?", (form_data.username,)).fetchone()
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["username"]}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

# 3. Проверка текущего пользователя
@app.get("/api/me")
def read_users_me(current_user: dict = Depends(get_current_user)):
    return {"id": current_user["id"], "username": current_user["username"]}

# 4. Основной эндпоинт чата Rubinov AI
@app.post("/api/chat")
def chat_handler(payload: ChatRequest, current_user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    prompt = payload.prompt.strip()
    image_base64 = payload.image

    if not prompt and not image_base64:
        raise HTTPException(status_code=400, detail="Пустой запрос")

    # Сохраняем запрос пользователя
    db.execute("INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
               (current_user["id"], "user", prompt))
    db.commit()

    # Сценарий генерации изображений
    if prompt.lower().startswith("нарисуй") or prompt.lower().startswith("draw"):
        clean_prompt = prompt.split(":", 1)[-1].strip() if ":" in prompt else prompt
        bot_response = f"Изображение по запросу «{clean_prompt}» сформировано."
        image_url = "https://picsum.photos/800/600"  # Сда подключается Imagen API

        db.execute("INSERT INTO messages (user_id, role, content, image_url) VALUES (?, ?, ?, ?)",
                   (current_user["id"], "bot", bot_response, image_url))
        db.commit()

        return {"response": bot_response, "image_url": image_url}

    # Текстовый сценарий
    else:
        bot_response = f"Ответ Rubinov AI для {current_user['username']}: {prompt}"
        if image_base64:
            bot_response += " (Изображение успешно обработано)"

        db.execute("INSERT INTO messages (user_id, role, content) VALUES (?, ?, ?)",
                   (current_user["id"], "bot", bot_response))
        db.commit()

        return {"response": bot_response}

# 5. Получение истории чата
@app.get("/api/history")
def get_history(current_user: dict = Depends(get_current_user), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT role, content, image_url, created_at FROM messages WHERE user_id = ? ORDER BY id ASC",
        (current_user["id"],)
    ).fetchall()
    return {"history": [dict(r) for r in rows]}
