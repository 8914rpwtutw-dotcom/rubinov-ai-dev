import os
import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile, Cookie, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google import genai
from google.genai import types
from google.genai.errors import APIError

# --- БАЗА ДАННЫХ ---
DB_FILE = "rubinov_ai.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            is_vip INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

init_db()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# --- ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ---
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@rubinov.ai")

# --- GEMINI ИИ ---
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

def get_gemini_client(api_key: str):
    return genai.Client(api_key=api_key)

def get_gemini_response(prompt: str, file_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> str:
    global current_key_idx, current_model_idx
    
    lowered = prompt.lower()
    if "нарисуй" in lowered or "draw" in lowered or "сгенерируй" in lowered:
        clean_prompt = prompt.replace("Нарисуй:", "").replace("нарисуй", "").replace("сгенерируй", "").strip()
        if not clean_prompt:
            clean_prompt = "beautiful futuristic neon cyberpunk landscape"
        
        import urllib.parse
        encoded_prompt = urllib.parse.quote(clean_prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        return f"""Вот ваше сгенерированное изображение по запросу: *"{clean_prompt}"*

<div style="margin-top:14px;">
    <img src="{img_url}" alt="{clean_prompt}" style="max-width:100%; border-radius:16px; display:block; margin-bottom:12px; box-shadow: 0 12px 40px rgba(99, 102, 241, 0.2); border: 1px solid rgba(255,255,255,0.08);" />
    <a href="{img_url}" target="_blank" download="rubinov_ai.jpg" style="display:inline-flex; align-items: center; gap: 8px; background: linear-gradient(135deg, #6366f1, #a855f7); color:#fff; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:600; box-shadow: 0 4px 20px rgba(99, 102, 241, 0.35);">📥 Скачать в высоком разрешении</a>
</div>"""

    api_keys = get_api_keys()
    if not api_keys:
        raise HTTPException(status_code=500, detail="API-ключи не найдены.")

    num_keys = len(api_keys)
    num_models = len(MODELS)
    total_attempts = num_keys * num_models * 2

    contents = []
    if file_bytes and mime_type:
        contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))
    if prompt:
        contents.append(prompt)

    for attempt in range(total_attempts):
        active_key = api_keys[current_key_idx % num_keys]
        active_model = MODELS[current_model_idx % num_models]

        try:
            client = get_gemini_client(active_key)
            response = client.models.generate_content(model=active_model, contents=contents)
            return response.text
        except APIError as e:
            if e.code in [503, 429] or "RESOURCE_EXHAUSTED" in str(e):
                current_model_idx = (current_model_idx + 1) % num_models
                continue
            else:
                break
        except Exception:
            break

    raise HTTPException(status_code=500, detail="Сервис ИИ временно перегружен.")

# --- API АВТОРИЗАЦИИ ЧЕРЕЗ GOOGLE ---
@app.post("/api/auth/google")
def google_auth(credential: str = Form(...)):
    try:
        id_info = id_token.verify_oauth2_token(
            credential, 
            google_requests.Request(), 
            GOOGLE_CLIENT_ID if GOOGLE_CLIENT_ID else None
        )
        email = id_info.get("email", "").lower()
        
        if not email:
            raise HTTPException(status_code=400, detail="Не удалось получить Email из Google аккаунта.")

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (email) VALUES (?)", (email,))
        cursor.execute("SELECT is_banned, is_vip FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.commit()
        conn.close()

        if user and user[0]:
            raise HTTPException(status_code=403, detail="Пользователь заблокирован.")

        response = JSONResponse({"status": "success", "email": email, "is_admin": email == ADMIN_EMAIL.lower()})
        response.set_cookie(key="rubinov_user_email", value=str(email), httponly=True, max_age=86400 * 30)
        return response

    except ValueError:
        raise HTTPException(status_code=400, detail="Недействительный токен Google.")

@app.get("/api/auth/check")
def check_auth(rubinov_user_email: Optional[str] = Cookie(None)):
    if not rubinov_user_email:
        return {"authenticated": False, "client_id": GOOGLE_CLIENT_ID}
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip, is_banned FROM users WHERE email = ?", (rubinov_user_email,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return {"authenticated": False, "client_id": GOOGLE_CLIENT_ID}
    
    return {
        "authenticated": True, 
        "email": rubinov_user_email, 
        "is_vip": bool(user[0]),
        "is_banned": bool(user[1]),
        "is_admin": rubinov_user_email.lower() == ADMIN_EMAIL.lower(),
        "client_id": GOOGLE_CLIENT_ID
    }

@app.post("/api/chat")
async def chat_endpoint(
    prompt: str = Form(""),
    file: Optional[UploadFile] = File(None),
    rubinov_user_email: Optional[str] = Cookie(None)
):
    if not rubinov_user_email:
        raise HTTPException(status_code=401, detail="Требуется авторизация.")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned, is_vip FROM users WHERE email = ?", (rubinov_user_email,))
    user = cursor.fetchone()
    conn.close()

    if not user or user[0]:
        raise HTTPException(status_code=403, detail="Доступ заблокирован.")
    
    is_vip = bool(user[1])
    if file and not is_vip:
        raise HTTPException(status_code=403, detail="Загрузка файлов доступна только для VIP пользователей!")

    file_bytes = await file.read() if file else None
    mime_type = file.content_type if file else None

    answer = get_gemini_response(prompt, file_bytes, mime_type)
    return {"response": answer}

# --- HTML ИНТЕРФЕЙС С ИНТЕГРАЦИЕЙ GOOGLE SIGN-IN ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Rubinov AI</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <script src="https://accounts.google.com/gsi/client" async defer></script>
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
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); display: flex; }
        #auth-screen { position: fixed; inset: 0; background: var(--bg-main); z-index: 1000; display: flex; justify-content: center; align-items: center; }
        .auth-card { background: rgba(18, 21, 31, 0.85); border: 1px solid rgba(168, 85, 247, 0.2); backdrop-filter: blur(24px); padding: 40px 30px; border-radius: 24px; width: 90%; max-width: 380px; text-align: center; box-shadow: 0 20px 50px rgba(0,0,0,0.7); }
        .auth-card h2 { font-size: 24px; font-weight: 700; color: #fff; margin-bottom: 8px; }
        .auth-card p { font-size: 13px; color: var(--text-muted); line-height: 1.5; margin-bottom: 24px; }
        .google-btn-wrapper { display: flex; justify-content: center; width: 100%; }
        #ban-screen { position: fixed; inset: 0; background: rgba(4, 5, 8, 0.95); backdrop-filter: blur(15px); z-index: 2000; display: flex; justify-content: center; align-items: center; flex-direction: column; text-align: center; padding: 20px; }
        #ban-screen h1 { color: #f87171; font-size: 28px; margin-bottom: 10px; }
        .hidden { display: none !important; }
        #sidebar { width: 280px; min-width: 280px; background: var(--bg-sidebar); backdrop-filter: blur(24px); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 20px 14px; z-index: 50; height: 100dvh; }
        .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; padding: 0 4px; }
        .brand-logo-svg { width: 32px; height: 32px; filter: drop-shadow(0 0 12px rgba(168, 85, 247, 0.5)); }
        .brand h2 { font-size: 15px; font-weight: 700; color: #ffffff; }
        .btn-new-chat { background: var(--accent-gradient); color: #ffffff; border: none; padding: 11px 16px; border-radius: 14px; font-size: 12px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 9px; margin-bottom: 18px; }
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
        .chat-item { background: rgba(255, 255, 255, 0.015); border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; font-size: 12px; color: #cbd5e1; display: flex; justify-content: space-between; cursor: pointer; }
        .chat-item.active { background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.35); color: #ffffff; }
        .sidebar-footer { font-size: 11px; color: var(--text-muted); display: flex; flex-direction: column; gap: 8px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border-color); }
        #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); position: relative; height: 100dvh; overflow: hidden; }
        #chat-header { height: 60px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; padding: 0 24px; background: rgba(4, 5, 8, 0.5); }
        #chat-container { flex: 1; overflow-y: auto; padding: 24px 24px 140px 24px; display: flex; flex-direction: column; gap: 22px; max-width: 900px; width: 100%; margin: 0 auto; }
        .msg-row { display: flex; flex-direction: column; width: 100%; }
        .msg-row.user-row { align-items: flex-end; }
        .msg-row.bot-row { align-items: flex-start; }
        .msg-user { background: var(--user-msg-bg); color: #ffffff; border-radius: 18px 18px 4px 18px; padding: 13px 18px; font-size: 13.5px; max-width: 85%; }
        .msg-bot { background: var(--bot-msg-bg); border: 1px solid var(--border-color); color: #e2e8f0; border-radius: 18px 18px 18px 4px; padding: 18px 22px; font-size: 13.5px; max-width: 90%; }
        #input-wrapper { position: absolute; bottom: 0; left: 0; right: 0; padding: 16px 24px; background: linear-gradient(180deg, rgba(4,5,8,0) 0%, var(--bg-main) 40%); }
        #input-container { max-width: 900px; margin: 0 auto; background: rgba(13, 16, 24, 0.8); border: 1px solid rgba(168, 85, 247, 0.18); border-radius: 20px; padding: 8px 12px; display: flex; gap: 8px; }
        #prompt-input { flex: 1; background: transparent; border: none; color: #ffffff; font-size: 14px; outline: none; }
        .btn-action { background: var(--accent-gradient); color: #ffffff; border: none; border-radius: 12px; padding: 11px 20px; font-weight: 600; cursor: pointer; }
    </style>
</head>
<body>

    <div id="auth-screen">
        <div class="auth-card">
            <h2>Rubinov AI</h2>
            <p>Войдите через Google для доступа к нейросети</p>
            <div class="google-btn-wrapper">
                <div id="g_id_onload"></div>
                <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black" data-text="signin_with" data-size="large"></div>
            </div>
        </div>
    </div>

    <div id="ban-screen" class="hidden">
        <h1>Вы забанены!</h1>
        <p>Ваш аккаунт заблокирован администратором.</p>
    </div>

    <div id="sidebar">
        <div class="brand">
            <svg class="brand-logo-svg" viewBox="0 0 100 100" fill="none"><path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="#ff4b4b" stroke-width="4"/><circle cx="50" cy="48" r="14" fill="#ff4b4b" opacity="0.25"/></svg>
            <div><h2>Rubinov AI</h2></div>
        </div>
        <button class="btn-new-chat" onclick="createNewChat()">+ Новый диалог</button>
        <div id="chats-list"></div>
        <div class="sidebar-footer">
            <span id="user-status-text">Загрузка...</span>
            <button onclick="logout()" style="background:none; border:none; color:#f87171; cursor:pointer; text-align:left;">Выйти</button>
        </div>
    </div>

    <div id="main">
        <div id="chat-header"><h3 id="current-chat-title">Чаты</h3></div>
        <div id="chat-container"></div>
        <div id="input-wrapper">
            <div id="input-container">
                <input type="text" id="prompt-input" placeholder="Введите сообщение..." onkeydown="if(event.key==='Enter') sendMessage()" />
                <button class="btn-action" onclick="sendMessage()">Отправить</button>
            </div>
        </div>
    </div>

    <script>
        let chats = JSON.parse(localStorage.getItem('rubinov_chats_v4') || '[]');
        let currentChatId = localStorage.getItem('rubinov_active_chat_v4') || null;

        async function handleCredentialResponse(response) {
            let formData = new FormData();
            formData.append('credential', response.credential);
            let res = await fetch('/api/auth/google', { method: 'POST', body: formData });
            if (res.ok) {
                location.reload();
            } else {
                alert('Ошибка входа через Google!');
            }
        }

        async function initAuth() {
            let res = await fetch('/api/auth/check');
            let data = await res.json();

            if (data.authenticated) {
                document.getElementById('auth-screen').classList.add('hidden');
                document.getElementById('user-status-text').textContent = (data.is_vip ? "👑 VIP: " : "👤 ") + data.email;
                if (data.is_banned) document.getElementById('ban-screen').classList.remove('hidden');
            } else {
                document.getElementById('auth-screen').classList.remove('hidden');
                if (data.client_id) {
                    google.accounts.id.initialize({
                        client_id: data.client_id,
                        callback: handleCredentialResponse
                    });
                    google.accounts.id.renderButton(
                        document.querySelector(".g_id_signin"),
                        { theme: "filled_black", size: "large", shape: "pill" }
                    );
                }
            }
        }
        window.onload = initAuth;

        function initChatApp() {
            if (chats.length === 0) createNewChat();
            else if (!currentChatId || !chats.find(c => c.id === currentChatId)) currentChatId = chats[0].id;
            renderChats();
        }
        initChatApp();

        function renderChats() {
            const list = document.getElementById('chats-list');
            list.innerHTML = '';
            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `chat-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.onclick = () => { currentChatId = chat.id; saveState(); renderChats(); };
                item.innerHTML = `<span>${chat.name}</span><span onclick="event.stopPropagation(); deleteChat('${chat.id}')">×</span>`;
                list.appendChild(item);
            });
            const active = chats.find(c => c.id === currentChatId);
            if (active) {
                document.getElementById('current-chat-title').textContent = active.name;
                renderMessages(active.messages);
            }
        }

        function createNewChat() {
            const newChat = { id: Date.now().toString(), name: `Чат ${chats.length + 1}`, messages: [] };
            chats.push(newChat);
            currentChatId = newChat.id;
            saveState();
            renderChats();
        }

        function deleteChat(id) {
            chats = chats.filter(c => c.id !== id);
            if (chats.length > 0 && currentChatId === id) currentChatId = chats[0].id;
            else if (chats.length === 0) createNewChat();
            saveState();
            renderChats();
        }

        function renderMessages(messages) {
            const container = document.getElementById('chat-container');
            container.innerHTML = '';
            messages.forEach(msg => {
                const row = document.createElement('div');
                row.className = `msg-row ${msg.role === 'user' ? 'user-row' : 'bot-row'}`;
                const box = document.createElement('div');
                box.className = msg.role === 'user' ? 'msg-user' : 'msg-bot';
                box.innerHTML = msg.role === 'user' ? msg.text : marked.parse(msg.text);
                row.appendChild(box);
                container.appendChild(row);
            });
            container.scrollTop = container.scrollHeight;
        }

        async function sendMessage() {
            const activeChat = chats.find(c => c.id === currentChatId);
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text) return;

            activeChat.messages.push({ role: 'user', text });
            renderMessages(activeChat.messages);
            input.value = '';

            const formData = new FormData();
            formData.append('prompt', text);

            try {
                const res = await fetch('/api/chat', { method: 'POST', body: formData });
                const data = await res.json();
                if (res.ok) activeChat.messages.push({ role: 'bot', text: data.response });
                else activeChat.messages.push({ role: 'bot', text: 'Ошибка: ' + (data.detail || 'Не удалось получить ответ.') });
            } catch (err) {
                activeChat.messages.push({ role: 'bot', text: 'Ошибка соединения.' });
            }
            saveState();
            renderChats();
        }

        function saveState() {
            localStorage.setItem('rubinov_chats_v4', JSON.stringify(chats));
            localStorage.setItem('rubinov_active_chat_v4', currentChatId);
        }

        function logout() {
            document.cookie = "rubinov_user_email=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            location.reload();
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_TEMPLATE

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
