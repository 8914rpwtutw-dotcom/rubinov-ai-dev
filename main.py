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
            name TEXT,
            picture TEXT,
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
        name = id_info.get("name", "Пользователь")
        picture = id_info.get("picture", "")

        if not email:
            raise HTTPException(status_code=400, detail="Не удалось получить Email.")

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO users (email, name, picture) VALUES (?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET name=excluded.name, picture=excluded.picture
        """, (email, name, picture))
        cursor.execute("SELECT is_banned, is_vip FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.commit()
        conn.close()

        if user and user[0]:
            raise HTTPException(status_code=403, detail="Пользователь заблокирован.")

        response = JSONResponse({
            "status": "success", 
            "email": email, 
            "name": name, 
            "picture": picture,
            "is_admin": email == ADMIN_EMAIL.lower()
        })
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
    cursor.execute("SELECT name, picture, is_vip, is_banned FROM users WHERE email = ?", (rubinov_user_email,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        return {"authenticated": False, "client_id": GOOGLE_CLIENT_ID}
    
    return {
        "authenticated": True, 
        "email": rubinov_user_email, 
        "name": user[0] or rubinov_user_email.split('@')[0],
        "picture": user[1] or "",
        "is_vip": bool(user[2]),
        "is_banned": bool(user[3]),
        "is_admin": rubinov_user_email.lower() == ADMIN_EMAIL.lower(),
        "client_id": GOOGLE_CLIENT_ID
    }

@app.post("/api/chat")
async def chat_endpoint(
    prompt: str = Form(""),
    file: Optional[UploadFile] = File(None),
    rubinov_user_email: Optional[str] = Cookie(None)
):
    if rubinov_user_email:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned, is_vip FROM users WHERE email = ?", (rubinov_user_email,))
        user = cursor.fetchone()
        conn.close()

        if user and user[0]:
            raise HTTPException(status_code=403, detail="Доступ заблокирован.")
        
        is_vip = bool(user[1]) if user else False
        if file and not is_vip:
            raise HTTPException(status_code=403, detail="Загрузка файлов доступна только для VIP пользователей!")

    file_bytes = await file.read() if file else None
    mime_type = file.content_type if file else None

    answer = get_gemini_response(prompt, file_bytes, mime_type)
    return {"response": answer}

# --- HTML ИНТЕРФЕЙС ИЗ ВИДЕО (1 в 1) ---
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
            --bg-main: #06070B;
            --bg-sidebar: #0B0D13;
            --border-color: rgba(255, 255, 255, 0.07);
            --accent-purple: #7C3AED;
            --accent-gradient: linear-gradient(135deg, #7C3AED 0%, #C084FC 100%);
            --text-main: #F3F4F6;
            --text-muted: #9CA3AF;
            --user-msg-bg: #1E1B4B;
            --bot-msg-bg: #0F111A;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); display: flex; }

        /* Модальное окно входа при лимите */
        #auth-modal { position: fixed; inset: 0; background: rgba(0,0,0,0.8); backdrop-filter: blur(12px); z-index: 1000; display: flex; justify-content: center; align-items: center; }
        .auth-card { background: #0F111A; border: 1px solid var(--border-color); padding: 36px 28px; border-radius: 24px; width: 90%; max-width: 380px; text-align: center; position: relative; }
        .auth-card h2 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 8px; }
        .auth-card p { font-size: 13px; color: var(--text-muted); margin-bottom: 24px; line-height: 1.5; }
        .close-modal { position: absolute; top: 16px; right: 16px; background: none; border: none; color: var(--text-muted); font-size: 20px; cursor: pointer; }

        #ban-screen { position: fixed; inset: 0; background: #06070B; z-index: 2000; display: flex; justify-content: center; align-items: center; flex-direction: column; text-align: center; }

        .hidden { display: none !important; }

        /* Сайдбар */
        #sidebar { width: 260px; min-width: 260px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 18px 14px; z-index: 50; height: 100dvh; }
        .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; padding: 0 4px; }
        .brand-logo-svg { width: 32px; height: 32px; filter: drop-shadow(0 0 10px rgba(124, 58, 237, 0.6)); }
        .brand h2 { font-size: 16px; font-weight: 700; color: #ffffff; }
        .btn-new-chat { background: var(--accent-gradient); color: #ffffff; border: none; padding: 12px 16px; border-radius: 12px; font-size: 13px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 8px; margin-bottom: 18px; transition: opacity 0.2s; }
        .btn-new-chat:hover { opacity: 0.9; }

        .chats-title { font-size: 11px; font-weight: 600; text-transform: uppercase; color: var(--text-muted); margin-bottom: 8px; padding: 0 4px; }
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
        .chat-item { border: 1px solid transparent; border-radius: 10px; padding: 10px 12px; font-size: 13px; color: #9CA3AF; display: flex; justify-content: space-between; align-items: center; cursor: pointer; }
        .chat-item.active { background: rgba(124, 58, 237, 0.15); border-color: rgba(124, 58, 237, 0.4); color: #ffffff; }
        .chat-item:hover:not(.active) { background: rgba(255,255,255,0.03); }

        /* Профиль внизу сайдбара */
        .sidebar-footer { padding-top: 14px; border-top: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; }
        .user-profile { display: flex; align-items: center; gap: 10px; overflow: hidden; }
        .user-avatar { width: 34px; height: 34px; border-radius: 50%; object-fit: cover; background: #1E1B4B; display: flex; align-items: center; justify-content: center; font-weight: 700; color: #C084FC; }
        .user-info { display: flex; flex-direction: column; overflow: hidden; }
        .user-name { font-size: 13px; font-weight: 600; color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .user-sub { font-size: 11px; color: var(--text-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .btn-logout { background: none; border: none; color: #F87171; cursor: pointer; font-size: 16px; padding: 4px; }
        .btn-login-sidebar { background: rgba(124, 58, 237, 0.2); border: 1px solid rgba(124, 58, 237, 0.4); color: #fff; width: 100%; padding: 10px; border-radius: 10px; font-size: 12px; font-weight: 600; cursor: pointer; text-align: center; }

        /* Главный контейнер чата */
        #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); position: relative; height: 100dvh; overflow: hidden; }
        #chat-header { height: 56px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; padding: 0 20px; background: rgba(6,7,11,0.8); backdrop-filter: blur(10px); }
        #chat-header h3 { font-size: 14px; font-weight: 600; }

        /* РАСТЯНУТЫЙ ЧАТ (без ограничений по ширине) */
        #chat-container { flex: 1; overflow-y: auto; padding: 24px 32px 140px 32px; display: flex; flex-direction: column; gap: 20px; width: 100%; }

        .hero-welcome { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; text-align: center; }
        .hero-logo { width: 64px; height: 64px; margin-bottom: 16px; filter: drop-shadow(0 0 20px rgba(124, 58, 237, 0.5)); }
        .hero-title { font-size: 26px; font-weight: 700; margin-bottom: 8px; }
        .hero-subtitle { font-size: 14px; color: var(--text-muted); }

        .msg-row { display: flex; flex-direction: column; width: 100%; }
        .msg-row.user-row { align-items: flex-end; }
        .msg-row.bot-row { align-items: flex-start; }
        .msg-user { background: var(--user-msg-bg); color: #ffffff; border-radius: 16px 16px 4px 16px; padding: 12px 18px; font-size: 14px; max-width: 80%; line-height: 1.5; }
        .msg-bot { background: var(--bot-msg-bg); border: 1px solid var(--border-color); color: #E5E7EB; border-radius: 16px 16px 16px 4px; padding: 16px 20px; font-size: 14px; max-width: 95%; line-height: 1.6; width: 100%; }
        
        .msg-status-text { font-size: 11px; color: var(--text-muted); margin-top: 4px; padding: 0 4px; }

        /* Анимация "Думаю..." */
        .thinking-box { display: flex; align-items: center; gap: 8px; color: var(--text-muted); font-size: 13px; }
        .dots-loader span { display: inline-block; width: 4px; height: 4px; border-radius: 50%; background: var(--text-muted); margin: 0 2px; animation: dots 1.4s infinite ease-in-out both; }
        .dots-loader span:nth-child(1) { animation-delay: -0.32s; }
        .dots-loader span:nth-child(2) { animation-delay: -0.16s; }
        @keyframes dots { 0%, 80%, 100% { transform: scale(0); } 40% { transform: scale(1); } }

        /* Поле ввода во всю ширину */
        #input-wrapper { position: absolute; bottom: 0; left: 0; right: 0; padding: 20px 32px; background: linear-gradient(180deg, rgba(6,7,11,0) 0%, var(--bg-main) 50%); }
        #input-container { width: 100%; background: #0F111A; border: 1px solid var(--border-color); border-radius: 16px; padding: 8px 14px; display: flex; align-items: center; gap: 10px; }
        #prompt-input { flex: 1; background: transparent; border: none; color: #ffffff; font-size: 14px; outline: none; padding: 6px 0; }
        
        .btn-icon { background: none; border: none; color: var(--text-muted); cursor: pointer; padding: 6px; display: flex; align-items: center; justify-content: center; border-radius: 8px; transition: color 0.2s; }
        .btn-icon:hover { color: #fff; background: rgba(255,255,255,0.05); }

        .btn-send { background: var(--accent-gradient); color: #ffffff; border: none; border-radius: 10px; padding: 10px 20px; font-weight: 600; font-size: 13px; cursor: pointer; transition: opacity 0.2s; }
        .btn-send:hover { opacity: 0.9; }
        .btn-cancel { background: rgba(239, 68, 68, 0.2); color: #F87171; border: 1px solid rgba(239, 68, 68, 0.4); border-radius: 10px; padding: 10px 18px; font-weight: 600; font-size: 13px; cursor: pointer; }

        .online-badge { position: absolute; bottom: 12px; left: 14px; font-size: 11px; color: #10B981; display: flex; align-items: center; gap: 6px; }
        .online-dot { width: 6px; height: 6px; background: #10B981; border-radius: 50%; }
    </style>
</head>
<body>

    <!-- Модалка авторизации Google (при 10 запросах) -->
    <div id="auth-modal" class="hidden">
        <div class="auth-card">
            <button class="close-modal" onclick="closeAuthModal()">×</button>
            <h2>Rubinov AI</h2>
            <p>Вы исчерпали 10 бесплатных гостевых запросов. Войдите через Google, чтобы продолжить пользоваться без ограничений!</p>
            <div style="display:flex; justify-content:center; width:100%;">
                <div id="g_id_onload"></div>
                <div class="g_id_signin" data-type="standard" data-shape="pill" data-theme="filled_black" data-text="signin_with" data-size="large"></div>
            </div>
        </div>
    </div>

    <!-- Экран бана -->
    <div id="ban-screen" class="hidden">
        <h1 style="color:#F87171; font-size:26px; margin-bottom:10px;">Доступ ограничен</h1>
        <p style="color:#9CA3AF;">Ваш аккаунт был заблокирован администратором.</p>
    </div>

    <!-- Сайдбар -->
    <div id="sidebar">
        <div class="brand">
            <svg class="brand-logo-svg" viewBox="0 0 100 100" fill="none"><path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="#7C3AED" stroke-width="5"/><circle cx="50" cy="48" r="14" fill="#7C3AED" opacity="0.3"/></svg>
            <h2>Rubinov AI</h2>
        </div>

        <button class="btn-new-chat" onclick="createNewChat()">+ Новый диалог</button>

        <div class="chats-title">Чаты (<span id="chats-count">0/5</span>)</div>
        <div id="chats-list"></div>

        <div class="sidebar-footer" id="sidebar-footer">
            <!-- Заполняется динамически (профиль или войти) -->
        </div>
    </div>

    <!-- Главная область -->
    <div id="main">
        <div id="chat-header">
            <h3 id="current-chat-title">Новый чат</h3>
        </div>

        <div id="chat-container">
            <!-- Сообщения чата -->
        </div>

        <div id="input-wrapper">
            <div id="input-container">
                <button class="btn-icon" title="Прикрепить файл">
                    <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
                </button>
                <button class="btn-icon" title="Сгенерировать картинку">
                    <svg width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/><path d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/></svg>
                </button>
                
                <input type="text" id="prompt-input" placeholder="Введите сообщение или опишите картинку..." onkeydown="if(event.key==='Enter') handleSendClick()" />
                
                <div id="action-btn-container">
                    <button class="btn-send" onclick="handleSendClick()">Отправить</button>
                </div>
            </div>
        </div>

        <div class="online-badge">
            <div class="online-dot"></div> Online
        </div>
    </div>

    <script>
        let chats = JSON.parse(localStorage.getItem('rubinov_chats_v5') || '[]');
        let currentChatId = localStorage.getItem('rubinov_active_chat_v5') || null;
        let guestRequestsCount = parseInt(localStorage.getItem('rubinov_guest_req_count') || '0');
        let currentUser = null;
        let isGenerating = false;
        let currentAbortController = null;

        async function handleCredentialResponse(response) {
            let formData = new FormData();
            formData.append('credential', response.credential);
            let res = await fetch('/api/auth/google', { method: 'POST', body: formData });
            if (res.ok) {
                location.reload();
            } else {
                alert('Ошибка авторизации через Google');
            }
        }

        async function initAuth() {
            let res = await fetch('/api/auth/check');
            let data = await res.json();

            if (data.authenticated) {
                currentUser = data;
                if (data.is_banned) {
                    document.getElementById('ban-screen').classList.remove('hidden');
                    return;
                }
                renderProfileFooter();
            } else {
                renderGuestFooter();
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

        function renderProfileFooter() {
            const footer = document.getElementById('sidebar-footer');
            const avatarHtml = currentUser.picture 
                ? `<img src="${currentUser.picture}" class="user-avatar" />`
                : `<div class="user-avatar">${currentUser.name.charAt(0).toUpperCase()}</div>`;

            footer.innerHTML = `
                <div class="user-profile">
                    ${avatarHtml}
                    <div class="user-info">
                        <span class="user-name">${currentUser.is_vip ? '👑 ' : ''}${currentUser.name}</span>
                        <span class="user-sub">${currentUser.email}</span>
                    </div>
                </div>
                <button class="btn-logout" onclick="logout()" title="Выйти">✕</button>
            `;
        }

        function renderGuestFooter() {
            const footer = document.getElementById('sidebar-footer');
            footer.innerHTML = `
                <div style="width:100%;">
                    <div style="font-size:11px; color:var(--text-muted); margin-bottom:8px; text-align:center;">
                        Бесплатные запросы: ${guestRequestsCount}/10
                    </div>
                    <button class="btn-login-sidebar" onclick="openAuthModal()">Войти через Google</button>
                </div>
            `;
        }

        function openAuthModal() { document.getElementById('auth-modal').classList.remove('hidden'); }
        function closeAuthModal() { document.getElementById('auth-modal').classList.add('hidden'); }

        // Инициализация Чатов
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
                item.onclick = () => { if (!isGenerating) { currentChatId = chat.id; saveState(); renderChats(); } };
                item.innerHTML = `<span>${chat.name}</span><span onclick="event.stopPropagation(); deleteChat('${chat.id}')">×</span>`;
                list.appendChild(item);
            });
            document.getElementById('chats-count').textContent = `${chats.length}/5`;

            const active = chats.find(c => c.id === currentChatId);
            if (active) {
                document.getElementById('current-chat-title').textContent = active.name;
                renderMessages(active.messages);
            }
        }

        function createNewChat() {
            if (isGenerating) return;
            if (chats.length >= 5) {
                alert('Максимум 5 диалогов. Удалите старый диалог.');
                return;
            }
            const newChat = { id: Date.now().toString(), name: `Новый чат ${chats.length + 1}`, messages: [] };
            chats.push(newChat);
            currentChatId = newChat.id;
            saveState();
            renderChats();
        }

        function deleteChat(id) {
            if (isGenerating) return;
            chats = chats.filter(c => c.id !== id);
            if (chats.length > 0 && currentChatId === id) currentChatId = chats[0].id;
            else if (chats.length === 0) createNewChat();
            saveState();
            renderChats();
        }

        function renderMessages(messages) {
            const container = document.getElementById('chat-container');
            container.innerHTML = '';

            if (messages.length === 0) {
                container.innerHTML = `
                    <div class="hero-welcome">
                        <svg class="hero-logo" viewBox="0 0 100 100" fill="none"><path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="#7C3AED" stroke-width="4"/><circle cx="50" cy="48" r="14" fill="#7C3AED" opacity="0.3"/></svg>
                        <h1 class="hero-title">Rubinov AI</h1>
                        <p class="hero-subtitle">Чем я могу помочь вам сегодня?</p>
                    </div>
                `;
                return;
            }

            messages.forEach(msg => {
                const row = document.createElement('div');
                row.className = `msg-row ${msg.role === 'user' ? 'user-row' : 'bot-row'}`;
                const box = document.createElement('div');
                box.className = msg.role === 'user' ? 'msg-user' : 'msg-bot';

                if (msg.thinking) {
                    box.innerHTML = `
                        <div class="thinking-box">
                            Думаю... <div class="dots-loader"><span></span><span></span><span></span></div>
                        </div>
                    `;
                } else {
                    box.innerHTML = msg.role === 'user' ? msg.text : marked.parse(msg.text);
                }

                row.appendChild(box);

                if (msg.canceled) {
                    const status = document.createElement('div');
                    status.className = 'msg-status-text';
                    status.textContent = 'сообщение отменено';
                    row.appendChild(status);
                }

                container.appendChild(row);
            });
            container.scrollTop = container.scrollHeight;
        }

        async function handleSendClick() {
            if (isGenerating) return;

            // Проверка лимита гостей
            if (!currentUser && guestRequestsCount >= 10) {
                openAuthModal();
                return;
            }

            const activeChat = chats.find(c => c.id === currentChatId);
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text) return;

            if (!currentUser) {
                guestRequestsCount++;
                localStorage.setItem('rubinov_guest_req_count', guestRequestsCount.toString());
                renderGuestFooter();
            }

            activeChat.messages.push({ role: 'user', text });
            
            // Временное сообщение ИИ
            const botMsgIndex = activeChat.messages.length;
            activeChat.messages.push({ role: 'bot', text: '', thinking: true });

            renderMessages(activeChat.messages);
            input.value = '';

            setGeneratingState(true);

            currentAbortController = new AbortController();

            const formData = new FormData();
            formData.append('prompt', text);

            try {
                const res = await fetch('/api/chat', { 
                    method: 'POST', 
                    body: formData,
                    signal: currentAbortController.signal
                });
                const data = await res.json();
                
                activeChat.messages[botMsgIndex].thinking = false;
                if (res.ok) {
                    activeChat.messages[botMsgIndex].text = data.response;
                } else {
                    activeChat.messages[botMsgIndex].text = 'Ошибка: ' + (data.detail || 'Не удалось получить ответ.');
                }
            } catch (err) {
                if (err.name === 'AbortError') {
                    activeChat.messages[botMsgIndex].thinking = false;
                    activeChat.messages[botMsgIndex].text = '';
                    activeChat.messages[botMsgIndex].canceled = true;
                } else {
                    activeChat.messages[botMsgIndex].thinking = false;
                    activeChat.messages[botMsgIndex].text = 'Ошибка соединения.';
                }
            }

            setGeneratingState(false);
            saveState();
            renderMessages(activeChat.messages);
        }

        function cancelGeneration() {
            if (currentAbortController) {
                currentAbortController.abort();
            }
        }

        function setGeneratingState(generating) {
            isGenerating = generating;
            const container = document.getElementById('action-btn-container');
            const input = document.getElementById('prompt-input');
            input.disabled = generating;

            if (generating) {
                container.innerHTML = `<button class="btn-cancel" onclick="cancelGeneration()">Отменить</button>`;
            } else {
                container.innerHTML = `<button class="btn-send" onclick="handleSendClick()">Отправить</button>`;
            }
        }

        function saveState() {
            localStorage.setItem('rubinov_chats_v5', JSON.stringify(chats));
            localStorage.setItem('rubinov_active_chat_v5', currentChatId);
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
