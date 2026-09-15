import os
import time
import random
import sqlite3
import threading
import urllib.parse
from typing import Optional

try:
    from fastapi import FastAPI, HTTPException, File, Form, UploadFile, Depends
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    import jwt
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
    from aiogram import Bot, Dispatcher, F, types as aiogram_types
    from aiogram.filters import Command
except Exception as e:
    print(f"CRITICAL IMPORT ERROR: {e}")
    raise e

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

JWT_SECRET = os.getenv("JWT_SECRET", "rubinov_super_secret_key_2026_secure")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
security = HTTPBearer()

DB_NAME = "rubinov_secure.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT
        )
    ''')
    # Таблица временных одноразовых кодов для входа
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS login_codes (
            code TEXT PRIMARY KEY,
            telegram_id TEXT,
            expires_at REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload.get("telegram_id")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Недействительный токен авторизации.")

# --- ЭНДПОИНТ ПРОВЕРКИ КОДА ВХОДА ---
@app.post("/api/auth/verify-code")
def verify_login_code(code: str = Form(...)):
    code = code.strip()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT telegram_id, expires_at FROM login_codes WHERE code = ?", (code,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        raise HTTPException(status_code=400, detail="Неверный или несуществующий код.")
    
    telegram_id, expires_at = row
    
    if time.time() > expires_at:
        cursor.execute("DELETE FROM login_codes WHERE code = ?", (code,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Срок действия кода истек. Запросите новый в боте.")
    
    # Удаляем использованный код
    cursor.execute("DELETE FROM login_codes WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    
    # Выдаем JWT токен сессии
    token = jwt.encode({"telegram_id": telegram_id}, JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}

# --- ЛОГИКА ИИ (GEMINI) ---
MODELS = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-pro"]
current_key_idx = 0
current_model_idx = 0

def get_api_keys():
    keys = [
        os.getenv("GEMINI_KEY_1"),
        os.getenv("GEMINI_KEY_2"),
        os.getenv("GEMINI_KEY_3"),
        os.getenv("GEMINI_API_KEY")
    ]
    return [k.strip() for k in keys if k and k.strip()]

def get_gemini_response(prompt: str, file_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> str:
    global current_key_idx, current_model_idx
    
    lowered = prompt.lower()
    if "нарисуй" in lowered or "draw" in lowered or "сгенерируй" in lowered:
        clean_prompt = prompt.replace("Нарисуй:", "").replace("нарисуй", "").replace("сгенерируй", "").strip()
        if not clean_prompt:
            clean_prompt = "beautiful futuristic neon cyberpunk landscape"
        
        encoded_prompt = urllib.parse.quote(clean_prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        return f"""Вот ваше сгенерированное изображение по запросу: *"{clean_prompt}"*

<div style="margin-top:14px;">
    <img src="{img_url}" alt="{clean_prompt}" style="max-width:100%; border-radius:16px; display:block; margin-bottom:12px; box-shadow: 0 12px 40px rgba(99, 102, 241, 0.2); border: 1px solid rgba(255,255,255,0.08);" />
    <a href="{img_url}" target="_blank" download="rubinov_ai.jpg" style="display:inline-flex; align-items: center; gap: 8px; background: linear-gradient(135deg, #6366f1, #a855f7); color:#fff; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:600; box-shadow: 0 4px 20px rgba(99, 102, 241, 0.35);">📥 Скачать в высоком разрешении</a>
</div>"""

    api_keys = get_api_keys()
    if not api_keys:
        raise HTTPException(status_code=500, detail="API-ключи не найдены в Environment Variables.")

    num_keys = len(api_keys)
    num_models = len(MODELS)
    total_attempts = num_keys * num_models * 2

    contents = []
    if file_bytes and mime_type:
        contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))
    if prompt:
        contents.append(prompt)

    for _ in range(total_attempts):
        active_key = api_keys[current_key_idx % num_keys]
        active_model = MODELS[current_model_idx % num_models]

        try:
            client = genai.Client(api_key=active_key)
            response = client.models.generate_content(model=active_model, contents=contents)
            return response.text
        except APIError as e:
            if e.code in [503, 429] or "RESOURCE_EXHAUSTED" in str(e) or "UNAVAILABLE" in str(e):
                current_model_idx += 1
                if current_model_idx >= num_models:
                    current_model_idx = 0
                    current_key_idx = (current_key_idx + 1) % num_keys
                time.sleep(0.5)
                continue
            else:
                break
        except Exception:
            break

    raise HTTPException(status_code=500, detail="Сервис ИИ перегружен. Повторите попытку.")

@app.post("/api/chat")
async def chat_endpoint(
    prompt: str = Form(""),
    file: Optional[UploadFile] = File(None),
    telegram_id: str = Depends(get_current_user)
):
    if not prompt.strip() and not file:
        raise HTTPException(status_code=400, detail="Запрос или файл обязателен")
    
    file_bytes = awaitfile.read() if file else None
    mime_type = file.content_type if file else None

    answer = get_gemini_response(prompt, file_bytes, mime_type)
    return {"response": answer}


# --- ФРОНТЕНД САЙТА С ОКНОМ ВВОДА КОДА ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <title>Rubinov AI</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --bg-main: #040508;
            --bg-sidebar: rgba(10, 12, 18, 0.65);
            --border-color: rgba(255, 255, 255, 0.05);
            --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --user-msg-bg: linear-gradient(135deg, rgba(99, 102, 241, 0.16) 0%, rgba(168, 85, 247, 0.14) 100%);
            --bot-msg-bg: rgba(15, 18, 26, 0.65);
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; -webkit-tap-highlight-color: transparent; }
        html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); display: flex; }

        /* МОДАЛЬНОЕ ОКНО ВХОДА ПО КОДУ */
        #auth-modal {
            position: fixed; top: 0; left: 0; width: 100vw; height: 100dvh;
            background: rgba(4, 5, 8, 0.96); backdrop-filter: blur(25px);
            z-index: 1000; display: flex; align-items: center; justify-content: center;
            opacity: 0; pointer-events: none; transition: opacity 0.3s ease;
        }
        #auth-modal.active { opacity: 1; pointer-events: auto; }
        .auth-card {
            background: rgba(18, 21, 31, 0.95); border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 24px; padding: 32px; width: 90%; max-width: 400px;
            display: flex; flex-direction: column; gap: 16px; box-shadow: 0 20px 50px rgba(0,0,0,0.8);
            text-align: center; color: #fff;
        }
        .auth-card h2 { font-size: 20px; font-weight: 700; }
        .auth-card p { font-size: 13px; color: var(--text-muted); line-height: 1.5; }
        .auth-input {
            background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255,255,255,0.1);
            border-radius: 14px; padding: 12px 16px; color: #fff; font-size: 18px; outline: none; width: 100%; text-align: center; letter-spacing: 4px; font-weight: 700;
        }
        .auth-input:focus { border-color: rgba(168, 85, 247, 0.5); }
        .auth-btn {
            background: var(--accent-gradient); color: #fff; border: none; border-radius: 14px;
            padding: 12px; font-size: 14px; font-weight: 600; cursor: pointer; transition: 0.2s;
            box-shadow: 0 4px 20px rgba(99, 102, 241, 0.3);
        }
        .auth-btn:hover { transform: translateY(-1px); }
        .bot-link-hint { font-size: 12px; color: #a855f7; text-decoration: none; margin-top: 4px; display: inline-block; }

        /* ИНТЕРФЕЙС САЙТА */
        #sidebar { width: 280px; min-width: 280px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 20px 14px; height: 100dvh; }
        .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }
        .brand h2 { font-size: 15px; font-weight: 700; color: #fff; }
        .brand span { font-size: 10px; color: var(--text-muted); }
        .btn-new-chat { background: var(--accent-gradient); color: #fff; border: none; padding: 11px 16px; border-radius: 14px; font-size: 12px; font-weight: 600; cursor: pointer; margin-bottom: 18px; }
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
        .chat-item { background: rgba(255,255,255,0.015); border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; font-size: 12px; color: #cbd5e1; cursor: pointer; display: flex; justify-content: space-between; align-items: center; }
        .chat-item.active { background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.35); color: #fff; }
        
        #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); height: 100dvh; position: relative; }
        #chat-header { height: 60px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; padding: 0 24px; font-weight: 600; font-size: 14px; }
        #chat-container { flex: 1; overflow-y: auto; padding: 24px 24px 140px 24px; display: flex; flex-direction: column; gap: 22px; max-width: 900px; width: 100%; margin: 0 auto; }
        
        .msg-row { display: flex; flex-direction: column; width: 100%; }
        .msg-row.user-row { align-items: flex-end; }
        .msg-row.bot-row { align-items: flex-start; }
        .msg-user { background: var(--user-msg-bg); color: #fff; border: 1px solid rgba(168, 85, 247, 0.22); border-radius: 18px 18px 4px 18px; padding: 13px 18px; font-size: 13.5px; max-width: 85%; }
        .msg-bot { background: var(--bot-msg-bg); border: 1px solid var(--border-color); color: #e2e8f0; border-radius: 18px 18px 18px 4px; padding: 18px 22px; font-size: 13.5px; max-width: 90%; }
        
        #input-wrapper { position: absolute; bottom: 0; left: 0; right: 0; padding: 16px 24px; background: linear-gradient(180deg, transparent, var(--bg-main) 40%); }
        #input-container { max-width: 900px; margin: 0 auto; background: rgba(13, 16, 24, 0.8); border: 1px solid rgba(168, 85, 247, 0.18); border-radius: 20px; padding: 8px 12px; display: flex; gap: 8px; align-items: center; }
        #prompt-input { flex: 1; background: transparent; border: none; color: #fff; font-size: 14px; outline: none; padding: 6px; }
        .btn-action { background: var(--accent-gradient); color: #fff; border: none; border-radius: 12px; padding: 11px 20px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
    </style>
</head>
<body>
    <!-- Модальное окно авторизации по коду -->
    <div id="auth-modal" class="active">
        <div class="auth-card">
            <h2>Авторизация в Rubinov AI</h2>
            <p>Напишите нашему боту в Telegram команду <b>/login</b>, чтобы получить одноразовый код подтверждения.</p>
            <input type="text" id="code-input" class="auth-input" placeholder="000000" maxlength="6">
            <button class="auth-btn" onclick="submitCode()">Войти</button>
            <a href="https://t.me/" target="_blank" class="bot-link-hint" id="bot-link">Открыть Telegram-бота</a>
        </div>
    </div>

    <div id="sidebar">
        <div class="brand">
            <div><h2>Rubinov AI</h2><span>Secure Auth Edition</span></div>
        </div>
        <button class="btn-new-chat" onclick="createNewChat()">+ Новый диалог</button>
        <div id="chats-list"></div>
    </div>

    <div id="main">
        <div id="chat-header"><span id="current-chat-title">Новый чат 1</span></div>
        <div id="chat-container"></div>
        <div id="input-wrapper">
            <div id="input-container">
                <input type="text" id="prompt-input" placeholder="Введите сообщение или попросите нарисовать..." onkeydown="if(event.key==='Enter') sendMessage()" />
                <button class="btn-action" onclick="sendMessage()">Отправить</button>
            </div>
        </div>
    </div>

    <script>
        let token = localStorage.getItem('rubinov_token');
        let chats = JSON.parse(localStorage.getItem('rubinov_chats') || '[]');
        let currentChatId = localStorage.getItem('rubinov_active_chat') || null;

        if (token) {
            document.getElementById('auth-modal').classList.remove('active');
        }

        async function submitCode() {
            const code = document.getElementById('code-input').value.trim();
            if (!code) return alert('Введите код!');

            try {
                const res = await fetch('/api/auth/verify-code', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                    body: new URLSearchParams({ code: code })
                });
                const data = await res.json();
                if (res.ok) {
                    localStorage.setItem('rubinov_token', data.access_token);
                    token = data.access_token;
                    document.getElementById('auth-modal').classList.remove('active');
                } else {
                    alert(data.detail || 'Неверный код');
                }
            } catch (e) {
                alert('Ошибка соединения с сервером');
            }
        }

        if (chats.length === 0) {
            const initial = { id: Date.now().toString(), name: 'Новый чат 1', messages: [] };
            chats.push(initial);
            currentChatId = initial.id;
            saveState();
        }

        function saveState() {
            localStorage.setItem('rubinov_chats', JSON.stringify(chats));
            localStorage.setItem('rubinov_active_chat', currentChatId);
            renderChats();
        }

        function renderChats() {
            const list = document.getElementById('chats-list');
            list.innerHTML = '';
            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `chat-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.textContent = chat.name;
                item.onclick = () => { currentChatId = chat.id; saveState(); };
                list.appendChild(item);
            });
            const active = chats.find(c => c.id === currentChatId);
            if (active) {
                document.getElementById('current-chat-title').textContent = active.name;
                renderMessages(active.messages);
            }
        }

        function createNewChat() {
            if (chats.length >= 5) return alert('Максимум 5 чатов!');
            const newChat = { id: Date.now().toString(), name: `Новый чат ${chats.length + 1}`, messages: [] };
            chats.push(newChat);
            currentChatId = newChat.id;
            saveState();
        }

        function renderMessages(messages) {
            const container = document.getElementById('chat-container');
            container.innerHTML = '';
            messages.forEach(msg => {
                const row = document.createElement('div');
                row.className = `msg-row ${msg.role === 'user' ? 'user-row' : 'bot-row'}`;
                const box = document.createElement('div');
                box.className = msg.role === 'user' ? 'msg-user' : 'msg-bot';
                if (msg.role === 'bot' && msg.text.includes('<img')) {
                    box.innerHTML = msg.text;
                } else {
                    box.innerHTML = msg.role === 'bot' ? marked.parse(msg.text) : escapeHtml(msg.text);
                }
                row.appendChild(box);
                container.appendChild(row);
            });
            container.scrollTop = container.scrollHeight;
        }

        async function sendMessage() {
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text) return;

            const activeChat = chats.find(c => c.id === currentChatId);
            activeChat.messages.push({ role: 'user', text: text });
            if (activeChat.messages.length === 1) activeChat.name = text.slice(0, 18);
            renderMessages(activeChat.messages);
            input.value = '';

            const formData = new FormData();
            formData.append('prompt', text);

            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + token },
                    body: formData
                });
                const data = await res.json();
                if (res.ok) {
                    activeChat.messages.push({ role: 'bot', text: data.response });
                } else {
                    if (res.status === 401) {
                        localStorage.removeItem('rubinov_token');
                        location.reload();
                    } else {
                        activeChat.messages.push({ role: 'bot', text: 'Ошибка: ' + data.detail });
                    }
                }
            } catch (e) {
                activeChat.messages.push({ role: 'bot', text: 'Ошибка соединения.' });
            }
            saveState();
        }

        function escapeHtml(text) {
            return (text || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        renderChats();
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_root():
    return HTML_TEMPLATE


# --- ВСТРОЕННЫЙ TELEGRAM-БОТ (ОБРАБОТКА /login) ---
async def start_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        print("Telegram bot token not found, bot disabled.")
        return
    
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def cmd_start(message: aiogram_types.Message):
        await message.answer(
            "👋 Привет! Я бот-помощник **Rubinov AI**.\n\n"
            "Чтобы войти в свой веб-кабинет, отправьте мне команду:\n👉 /login"
        )

    @dp.message(Command("login"))
    async def cmd_login(message: aiogram_types.Message):
        telegram_id = str(message.from_user.id)
        username = message.from_user.username or message.from_user.first_name
        
        # Генерируем случайный 6-значный код
        code = str(random.randint(100000, 999999))
        expires_at = time.time() + 60  # код действителен 1 минуту
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO users (telegram_id, username) VALUES (?, ?)", (telegram_id, username))
        cursor.execute("INSERT OR REPLACE INTO login_codes (code, telegram_id, expires_at) VALUES (?, ?, ?)", (code, telegram_id, expires_at))
        conn.commit()
        conn.close()
        
        await message.answer(
            f"🔐 Ваш одноразовый код для входа на сайт:\n\n`{code}`\n\n"
            "⚠️ *Код действителен в течение 1 минуты.* Введите его на сайте для авторизации.",
            parse_mode="Markdown"
        )

    print("Telegram bot polling started...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        print(f"Bot polling error: {e}")

# Запускаем телеграм-бота в фоновом потоке при старте FastAPI
@app.on_event("startup")
on_startup():
    threading.Thread(target=lambda: __import__('asyncio').run(start_telegram_bot()), daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
