import os
import time
import asyncio
import sqlite3
import threading
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile, Cookie, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from google import genai
from google.genai import types
from google.genai.errors import APIError

# Aiogram для Telegram бота
from aiogram import Bot, Dispatcher, F, types as aiogram_types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- БАЗА ДАННЫХ ---
DB_FILE = "rubinov_ai.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            is_vip INTEGER DEFAULT 0,
            is_banned INTEGER DEFAULT 0,
            last_ticket_time REAL DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS auth_codes (
            code TEXT PRIMARY KEY,
            telegram_id INTEGER,
            expires_at REAL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            message TEXT,
            status TEXT DEFAULT 'open'
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- TELEGRAM БОТ ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

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
        raise HTTPException(status_code=500, detail="API-ключи не найдены в переменных окружения.")

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
                time.sleep(0.3)
                continue
            else:
                break
        except Exception:
            break

    raise HTTPException(status_code=500, detail="Сервис ИИ перегружен. Повторите попытку.")

# --- ЛОГИКА TELEGRAM БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: aiogram_types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    
    cursor.execute("SELECT is_vip, is_banned FROM users WHERE telegram_id = ?", (user_id,))
    user_data = cursor.fetchone()
    conn.close()

    if user_data and user_data[1]:
        await message.answer("❌ Вы заблокированы.")
        return

    is_vip = user_data[0] if user_data else 0
    vip_status = "👑 VIP Активен" if is_vip else "👤 Стандарт"

    import random
    code = "".join(random.choices("0123456789", k=6))
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("REPLACE INTO auth_codes (code, telegram_id, expires_at) VALUES (?, ?, ?)", 
                   (code, user_id, time.time() + 300))
    conn.commit()
    conn.close()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Получить код для входа на сайт", callback_data=f"get_code_{code}")],
        [InlineKeyboardButton(text="💬 Написать в поддержку", callback_data="create_ticket")]
    ])

    if user_id == ADMIN_TELEGRAM_ID:
        keyboard.inline_keyboard.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin_panel")])

    await message.answer(
        f"👋 Добро пожаловать в **Rubinov AI**!\nСтатус: {vip_status}\n\nНажмите кнопку ниже для получения кода авторизации в веб-интерфейсе:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.callback_query(F.data.startswith("get_code_"))
async def process_get_code(callback: aiogram_types.CallbackQuery):
    code = callback.data.split("_")[2]
    await callback.message.answer(f"🔐 Ваш код авторизации на сайте: `{code}`\nДействителен 5 минут.", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "create_ticket")
async def process_ticket_start(callback: aiogram_types.CallbackQuery):
    await callback.message.answer("✍️ Отправьте ваше сообщение в поддержку одним сообщением:", parse_mode="Markdown")
    await callback.answer()

# --- API МАРШРУТЫ ---
@app.post("/api/auth/verify")
def verify_code(code: str = Form(...)):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, expires_at FROM auth_codes WHERE code = ?", (code,))
    row = cursor.fetchone()
    
    if not row or time.time() > row[1]:
        conn.close()
        raise HTTPException(status_code=400, detail="Неверный или просроченный код.")
    
    tg_id = row[0]
    cursor.execute("DELETE FROM auth_codes WHERE code = ?", (code,))
    cursor.execute("SELECT is_banned, is_vip FROM users WHERE telegram_id = ?", (tg_id,))
    user = cursor.fetchone()
    conn.close()

    if user and user[0]:
        raise HTTPException(status_code=403, detail="Пользователь заблокирован.")

    response = JSONResponse({"status": "success", "telegram_id": tg_id, "is_admin": tg_id == ADMIN_TELEGRAM_ID})
    response.set_cookie(key="rubinov_tg_id", value=str(tg_id), httponly=True, max_age=86400 * 30)
    return response

@app.get("/api/auth/check")
def check_auth(rubinov_tg_id: Optional[str] = Cookie(None)):
    if not rubinov_tg_id:
        return {"authenticated": False}
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT is_vip, is_banned FROM users WHERE telegram_id = ?", (rubinov_tg_id,))
    user = cursor.fetchone()
    conn.close()

    if not user or user[1]:
        return {"authenticated": False}
    
    return {
        "authenticated": True, 
        "telegram_id": rubinov_tg_id, 
        "is_vip": bool(user[0]),
        "is_admin": int(rubinov_tg_id) == ADMIN_TELEGRAM_ID
    }

@app.post("/api/chat")
async def chat_endpoint(
    prompt: str = Form(""),
    file: Optional[UploadFile] = File(None),
    rubinov_tg_id: Optional[str] = Cookie(None)
):
    if not rubinov_tg_id:
        raise HTTPException(status_code=401, detail="Требуется авторизация через бота.")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (rubinov_tg_id,))
    user = cursor.fetchone()
    conn.close()

    if not user or user[0]:
        raise HTTPException(status_code=403, detail="Доступ заблокирован.")

    file_bytes = await file.read() if file else None
    mime_type = file.content_type if file else None

    answer = get_gemini_response(prompt, file_bytes, mime_type)
    return {"response": answer}

@app.get("/api/admin/users")
def admin_get_users(rubinov_tg_id: Optional[str] = Cookie(None)):
    if not rubinov_tg_id or int(rubinov_tg_id) != ADMIN_TELEGRAM_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, username, is_vip, is_banned FROM users")
    users = [{"telegram_id": r[0], "username": r[1], "is_vip": r[2], "is_banned": r[3]} for r in cursor.fetchall()]
    conn.close()
    return {"users": users}

@app.post("/api/admin/action")
def admin_action(telegram_id: int = Form(...), action: str = Form(...), rubinov_tg_id: Optional[str] = Cookie(None)):
    if not rubinov_tg_id or int(rubinov_tg_id) != ADMIN_TELEGRAM_ID:
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if action == "toggle_vip":
        cursor.execute("UPDATE users SET is_vip = 1 - is_vip WHERE telegram_id = ?", (telegram_id,))
    elif action == "toggle_ban":
        cursor.execute("UPDATE users SET is_banned = 1 - is_banned WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


# --- ПОЛНЫЙ ОРИГИНАЛЬНЫЙ UI ИНТЕРФЕЙС С ИНТЕГРАЦИЕЙ АВТОРИЗАЦИИ И АДМИНКИ ---
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
            --card-bg: rgba(18, 21, 31, 0.5);
            --border-color: rgba(255, 255, 255, 0.05);
            --border-hover: rgba(168, 85, 247, 0.25);
            --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 100%);
            --accent-glow: rgba(168, 85, 247, 0.15);
            --cancel-bg: rgba(248, 113, 113, 0.1);
            --cancel-border: rgba(248, 113, 113, 0.3);
            --cancel-color: #f87171;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --user-msg-bg: linear-gradient(135deg, rgba(99, 102, 241, 0.16) 0%, rgba(168, 85, 247, 0.14) 100%);
            --bot-msg-bg: rgba(15, 18, 26, 0.65);
            --scrollbar-thumb: rgba(255, 255, 255, 0.08);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
        html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); }
        body { display: flex; position: relative; }

        /* ЭКРАН АВТОРИЗАЦИИ */
        #auth-screen {
            position: fixed; inset: 0; background: var(--bg-main); z-index: 1000;
            display: flex; justify-content: center; align-items: center;
        }
        .auth-card {
            background: rgba(18, 21, 31, 0.85); border: 1px solid rgba(168, 85, 247, 0.2);
            backdrop-filter: blur(24px); padding: 35px; border-radius: 24px; width: 90%; max-width: 400px;
            text-align: center; box-shadow: 0 20px 50px rgba(0,0,0,0.7), 0 0 30px rgba(168,85,247,0.1);
        }
        .auth-card h2 { font-size: 22px; font-weight: 700; color: #fff; margin-bottom: 10px; }
        .auth-card p { font-size: 13px; color: var(--text-muted); line-height: 1.5; margin-bottom: 20px; }
        .auth-input {
            width: 100%; padding: 12px; background: rgba(255,255,255,0.03); border: 1px solid var(--border-color);
            border-radius: 12px; color: #fff; font-size: 20px; text-align: center; letter-spacing: 6px; margin-bottom: 15px; outline: none;
        }
        .auth-input:focus { border-color: rgba(168, 85, 247, 0.5); }
        .auth-btn { background: var(--accent-gradient); color: #fff; border: none; padding: 12px; border-radius: 12px; font-weight: 600; cursor: pointer; width: 100%; font-size: 14px; }

        .hidden { display: none !important; }

        body::before {
            content: ''; position: fixed; top: -15vh; left: -15vw; width: 55vw; height: 55vh;
            background: radial-gradient(circle, rgba(99, 102, 241, 0.07) 0%, rgba(168, 85, 247, 0.02) 60%, transparent 80%);
            z-index: 0; pointer-events: none; filter: blur(80px);
        }

        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 20px; }

        #sidebar-overlay {
            display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100dvh;
            background: rgba(4, 5, 8, 0.7); backdrop-filter: blur(6px); z-index: 40; opacity: 0; transition: opacity 0.3s ease;
        }
        #sidebar-overlay.active { display: block; opacity: 1; }

        #sidebar { 
            width: 280px; min-width: 280px; background: var(--bg-sidebar); backdrop-filter: blur(24px);
            border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 20px 14px; 
            z-index: 50; height: 100dvh; transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), margin-left 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        
        body.sidebar-collapsed #sidebar { margin-left: -280px; }

        .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; padding: 0 4px; }
        .brand-logo-svg { width: 32px; height: 32px; filter: drop-shadow(0 0 12px rgba(168, 85, 247, 0.5)); flex-shrink: 0; }
        .brand h2 { font-size: 15px; font-weight: 700; color: #ffffff; }
        .brand span { font-size: 10px; color: var(--text-muted); display: block; }

        .btn-new-chat { 
            background: var(--accent-gradient); color: #ffffff; border: none; padding: 11px 16px; 
            border-radius: 14px; font-size: 12px; font-weight: 600; cursor: pointer; display: flex; align-items: center; gap: 9px; 
            margin-bottom: 18px; box-shadow: 0 4px 20px rgba(99, 102, 241, 0.3);
        }
        .chats-header { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); font-weight: 700; margin-bottom: 8px; padding: 0 4px; text-transform: uppercase; }
        
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding-right: 2px; }
        .chat-item { 
            background: rgba(255, 255, 255, 0.015); border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; 
            font-size: 12px; color: #cbd5e1; display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: all 0.2s ease;
        }
        .chat-item.active { background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.35); color: #ffffff; }
        .chat-item .close-btn { color: var(--text-muted); font-size: 14px; cursor: pointer; border-radius: 6px; width: 22px; height: 22px; display: flex; align-items: center; justify-content: center; }

        .sidebar-footer { font-size: 11px; color: var(--text-muted); display: flex; flex-direction: column; gap: 8px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border-color); }
        .status-row { display: flex; align-items: center; gap: 8px; }
        .status-dot { width: 7px; height: 7px; background: #a855f7; border-radius: 50%; box-shadow: 0 0 10px rgba(168, 85, 247, 0.8); }

        #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); position: relative; height: 100dvh; overflow: hidden; z-index: 1; }
        
        #chat-header { 
            height: 60px; min-height: 60px; border-bottom: 1px solid var(--border-color); 
            display: flex; align-items: center; justify-content: space-between; padding: 0 24px; background: rgba(4, 5, 8, 0.5); backdrop-filter: blur(16px); z-index: 10;
        }
        .header-left { display: flex; align-items: center; gap: 14px; }
        .menu-toggle { background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); color: #ffffff; border-radius: 12px; padding: 8px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
        #chat-header h3 { font-size: 14px; font-weight: 600; color: #ffffff; }

        #chat-container { flex: 1; overflow-y: auto; padding: 24px 24px 140px 24px; display: flex; flex-direction: column; gap: 22px; max-width: 900px; width: 100%; margin: 0 auto; position: relative; z-index: 2; }

        .welcome-screen {
            position: absolute; top: 45%; left: 50%; transform: translate(-50%, -50%); text-align: center; width: 90%; max-width: 480px; display: flex; flex-direction: column; align-items: center; gap: 16px;
        }
        .welcome-avatar-glow { padding: 20px; border-radius: 28px; background: rgba(168, 85, 247, 0.04); border: 1px solid rgba(168, 85, 247, 0.15); box-shadow: 0 0 50px rgba(168, 85, 247, 0.12); }
        .welcome-avatar-svg { width: 60px; height: 60px; filter: drop-shadow(0 0 18px rgba(168, 85, 247, 0.6)); }
        .welcome-screen h1 { font-size: 24px; font-weight: 700; color: #ffffff; }
        .welcome-screen p { font-size: 13.5px; color: var(--text-muted); line-height: 1.6; }
        
        .msg-row { display: flex; flex-direction: column; width: 100%; }
        .msg-row.user-row { align-items: flex-end; }
        .msg-row.bot-row { align-items: flex-start; }

        .msg-user { 
            background: var(--user-msg-bg); color: #ffffff; border: 1px solid rgba(168, 85, 247, 0.22); border-radius: 18px 18px 4px 18px; 
            padding: 13px 18px; font-size: 13.5px; line-height: 1.55; max-width: 85%; word-break: break-word; backdrop-filter: blur(12px);
        }
        .msg-bot { 
            background: var(--bot-msg-bg); border: 1px solid var(--border-color); color: #e2e8f0; border-radius: 18px 18px 18px 4px; 
            padding: 18px 22px; font-size: 13.5px; line-height: 1.65; max-width: 90%; word-break: break-word; backdrop-filter: blur(20px);
        }
        .msg-bot p { margin-bottom: 12px; }
        .msg-bot p:last-child { margin-bottom: 0; }
        .msg-bot strong { color: #ffffff; font-weight: 700; }

        .file-preview-tag { display: inline-flex; align-items: center; gap: 6px; background: rgba(168, 85, 247, 0.15); padding: 5px 10px; border-radius: 8px; font-size: 11px; margin-bottom: 8px; border: 1px solid rgba(168, 85, 247, 0.3); color: #d8b4fe; }

        .loader-box { display: flex; align-items: center; gap: 12px; background: var(--bot-msg-bg); border: 1px solid var(--border-color); border-radius: 18px; padding: 14px 20px; font-size: 13.5px; color: var(--text-muted); }
        .spinner { width: 16px; height: 16px; border: 2px solid rgba(168,85,247,0.2); border-top-color: #a855f7; border-radius: 50%; animation: spin 0.8s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }

        #input-wrapper { position: absolute; bottom: 0; left: 0; right: 0; padding: 16px 24px; background: linear-gradient(180deg, rgba(4,5,8,0) 0%, var(--bg-main) 40%); z-index: 20; }
        #input-container { max-width: 900px; margin: 0 auto; background: rgba(13, 16, 24, 0.8); backdrop-filter: blur(24px); border: 1px solid rgba(168, 85, 247, 0.18); border-radius: 20px; padding: 8px 12px; display: flex; flex-direction: column; gap: 8px; }
        
        #file-info-bar { display: none; align-items: center; justify-content: space-between; background: rgba(168, 85, 247, 0.12); padding: 6px 12px; border-radius: 10px; font-size: 11.5px; color: #d8b4fe; }
        .input-row { display: flex; gap: 8px; align-items: center; width: 100%; }
        .mini-btn { background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); color: var(--text-muted); padding: 10px; border-radius: 12px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
        #prompt-input { flex: 1; background: transparent; border: none; color: #ffffff; font-size: 14px; outline: none; padding: 6px 4px; }
        .btn-action { background: var(--accent-gradient); color: #ffffff; border: none; border-radius: 12px; padding: 11px 20px; font-size: 12.5px; font-weight: 600; cursor: pointer; }

        /* АДМИН МОДАЛКА */
        .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7); backdrop-filter: blur(5px); display: flex; justify-content: center; align-items: center; z-index: 200; }
        .modal-content { background: #0c1017; border: 1px solid var(--border-color); border-radius: 20px; padding: 30px; width: 100%; max-width: 600px; max-height: 80vh; overflow-y: auto; }
        table { width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 13px; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border-color); }
        th { color: var(--text-muted); }
    </style>
</head>
<body>

    <!-- ЭКРАН АВТОРИЗАЦИИ -->
    <div id="auth-screen">
        <div class="auth-card">
            <h2>Rubinov AI</h2>
            <p>Запустите Telegram-бота, нажмите кнопку <b>«Получить код для входа на сайт»</b> и введите 6-значный код ниже.</p>
            <input type="text" id="code-input" class="auth-input" placeholder="••••••" maxlength="6">
            <button class="auth-btn" onclick="verifyCode()">Войти в систему</button>
        </div>
    </div>

    <div id="sidebar-overlay" onclick="toggleSidebar()"></div>

    <!-- БОКОВАЯ ПАНЕЛЬ ИЗ ОРИГИНАЛА -->
    <div id="sidebar">
        <div class="brand">
            <svg class="brand-logo-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="#ff4b4b" stroke-width="4" fill="none" />
                <circle cx="50" cy="48" r="14" fill="#ff4b4b" opacity="0.25" />
                <path d="M42 45 Q46 40 50 45 Q54 40 58 45 Q60 52 50 56 Q40 52 42 45 Z" stroke="#ffffff" stroke-width="2.5" fill="none" />
            </svg>
            <div>
                <h2>Rubinov AI</h2>
                <span>Assistant</span>
            </div>
        </div>

        <button class="btn-new-chat" onclick="createNewChat()">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
            Новый диалог
        </button>

        <div class="chats-header">
            <span>Чаты (<span id="chat-count">1</span>/5)</span>
        </div>

        <div id="chats-list"></div>

        <div class="sidebar-footer">
            <button id="admin-btn" class="auth-btn hidden" style="padding:8px; font-size:12px; margin-bottom:5px;" onclick="openAdminPanel()">🛠 Админ-панель</button>
            <div class="status-row">
                <span class="status-dot"></span>
                <span id="user-status-text">Загрузка...</span>
            </div>
            <button onclick="logout()" style="background:none; border:none; color:#f87171; cursor:pointer; text-align:left; font-size:11px; padding:0;">Выйти из аккаунта</button>
        </div>
    </div>

    <div id="main">
        <div id="chat-header">
            <div class="header-left">
                <button class="menu-toggle" onclick="toggleSidebar()">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h18M3 6h18M3 18h18"/></svg>
                </button>
                <h3 id="current-chat-title">Чаты</h3>
            </div>
        </div>

        <div id="chat-container"></div>

        <div id="input-wrapper">
            <div id="input-container">
                <div id="file-info-bar">
                    <span id="file-name-text">Файл прикреплен</span>
                    <button onclick="removeSelectedFile()" style="background:none; border:none; color:#f87171; cursor:pointer;">✕</button>
                </div>
                <div class="input-row">
                    <input type="file" id="file-input" accept="image/*" style="display: none;" onchange="handleFileSelect(event)" />
                    <button class="mini-btn" onclick="document.getElementById('file-input').click()">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                    </button>
                    <input type="text" id="prompt-input" placeholder="Введите сообщение или опишите картинку..." onkeydown="handleKeyPress(event)" />
                    <button class="btn-action" onclick="sendMessage()">Отправить</button>
                </div>
            </div>
        </div>
    </div>

    <!-- АДМИН МОДАЛКА -->
    <div id="admin-modal" class="modal hidden">
        <div class="modal-content">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px;">
                <h2>Управление пользователями</h2>
                <button onclick="document.getElementById('admin-modal').classList.add('hidden')" style="background:none; border:none; color:#fff; font-size:18px; cursor:pointer;">✕</button>
            </div>
            <div id="users-table-container">Загрузка...</div>
        </div>
    </div>

    <script>
        let chats = JSON.parse(localStorage.getItem('rubinov_chats_main_v1') || '[]');
        let currentChatId = localStorage.getItem('rubinov_active_chat_main_v1') || null;
        let selectedFile = null;

        async function checkAuth() {
            let res = await fetch('/api/auth/check');
            let data = await res.json();
            if (data.authenticated) {
                document.getElementById('auth-screen').classList.add('hidden');
                document.getElementById('user-status-text').textContent = data.is_vip ? "👑 VIP Аккаунт" : "👤 Стандарт";
                if (data.is_admin) {
                    document.getElementById('admin-btn').classList.remove('hidden');
                }
                initChatApp();
            }
        }
        checkAuth();

        async function verifyCode() {
            let code = document.getElementById('code-input').value.trim();
            let formData = new FormData();
            formData.append('code', code);
            let res = await fetch('/api/auth/verify', { method: 'POST', body: formData });
            if (res.ok) {
                location.reload();
            } else {
                alert('Неверный или просроченный код!');
            }
        }

        function initChatApp() {
            if (chats.length === 0) {
                const initialChat = { id: Date.now().toString(), name: 'Новый чат 1', messages: [] };
                chats.push(initialChat);
                currentChatId = initialChat.id;
                saveState();
            } else if (!currentChatId || !chats.find(c => c.id === currentChatId)) {
                currentChatId = chats[0].id;
            }
            renderChats();
        }

        function saveState() {
            localStorage.setItem('rubinov_chats_main_v1', JSON.stringify(chats));
            localStorage.setItem('rubinov_active_chat_main_v1', currentChatId);
            renderChats();
        }

        function toggleSidebar() {
            if (window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.toggle('mobile-open');
                document.getElementById('sidebar-overlay').classList.toggle('active');
            } else {
                document.body.classList.toggle('sidebar-collapsed');
            }
        }

        function renderChats() {
            const list = document.getElementById('chats-list');
            list.innerHTML = '';
            document.getElementById('chat-count').textContent = chats.length;

            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `chat-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.onclick = () => { currentChatId = chat.id; saveState(); };
                item.innerHTML = `
                    <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:160px;">${escapeHtml(chat.name)}</span>
                    ${chats.length > 1 ? `<span class="close-btn" onclick="event.stopPropagation(); deleteChat('${chat.id}')">×</span>` : ''}
                `;
                list.appendChild(item);
            });

            const active = chats.find(c => c.id === currentChatId);
            if (active) {
                document.getElementById('current-chat-title').textContent = active.name;
                renderMessages(active.messages);
            }
        }

        function createNewChat() {
            if (chats.length >= 5) return;
            const newChat = { id: Date.now().toString(), name: `Новый чат ${chats.length + 1}`, messages: [] };
            chats.push(newChat);
            currentChatId = newChat.id;
            saveState();
        }

        function deleteChat(id) {
            chats = chats.filter(c => c.id !== id);
            if (currentChatId === id) currentChatId = chats[0].id;
            saveState();
        }

        function renderMessages(messages) {
            const container = document.getElementById('chat-container');
            container.innerHTML = '';

            if (messages.length === 0) {
                container.innerHTML = `
                    <div class="welcome-screen">
                        <div class="welcome-avatar-glow">
                            <svg class="welcome-avatar-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                                <path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="#ff4b4b" stroke-width="4" fill="none" />
                                <circle cx="50" cy="48" r="14" fill="#ff4b4b" opacity="0.25" />
                                <path d="M42 45 Q46 40 50 45 Q54 40 58 45 Q60 52 50 56 Q40 52 42 45 Z" stroke="#ffffff" stroke-width="2.5" fill="none" />
                            </svg>
                        </div>
                        <h1>Rubinov AI</h1>
                        <p>Чем я могу помочь вам сегодня?</p>
                    </div>`;
                return;
            }

            messages.forEach(msg => {
                const row = document.createElement('div');
                row.className = `msg-row ${msg.role === 'user' ? 'user-row' : 'bot-row'}`;
                const box = document.createElement('div');
                box.className = msg.role === 'user' ? 'msg-user' : 'msg-bot';

                if (msg.role === 'user') {
                    let content = msg.file ? `<div class="file-preview-tag">📷 ${escapeHtml(msg.file)}</div><br>` : '';
                    content += escapeHtml(msg.text);
                    box.innerHTML = content;
                } else {
                    box.innerHTML = msg.text.includes('<img') ? msg.text : marked.parse(msg.text);
                }
                row.appendChild(box);
                container.appendChild(row);
            });
            container.scrollTop = container.scrollHeight;
        }

        function handleFileSelect(e) {
            selectedFile = e.target.files[0];
            if (selectedFile) {
                document.getElementById('file-name-text').textContent = `📷 ${selectedFile.name}`;
                document.getElementById('file-info-bar').style.display = 'flex';
            }
        }

        function removeSelectedFile() {
            selectedFile = null;
            document.getElementById('file-input').value = '';
            document.getElementById('file-info-bar').style.display = 'none';
        }

        function handleKeyPress(e) {
            if (e.key === 'Enter') sendMessage();
        }

        async function sendMessage() {
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text && !selectedFile) return;

            const activeChat = chats.find(c => c.id === currentChatId);
            if (!activeChat) return;

            activeChat.messages.push({ role: 'user', text, file: selectedFile ? selectedFile.name : null });
            if (activeChat.messages.length === 1 && text) {
                activeChat.name = text.slice(0, 18) + (text.length > 18 ? '...' : '');
            }
            renderMessages(activeChat.messages);

            const formData = new FormData();
            formData.append('prompt', text);
            if (selectedFile) formData.append('file', selectedFile);

            input.value = '';
            removeSelectedFile();

            const container = document.getElementById('chat-container');
            const loaderRow = document.createElement('div');
            loaderRow.className = 'msg-row bot-row';
            loaderRow.id = 'loader-row';
            loaderRow.innerHTML = `<div class="loader-box"><div class="spinner"></div><span>Думаю...</span></div>`;
            container.appendChild(loaderRow);
            container.scrollTop = container.scrollHeight;

            try {
                const res = await fetch('/api/chat', { method: 'POST', body: formData });
                const data = await res.json();
                document.getElementById('loader-row')?.remove();

                if (res.ok) {
                    activeChat.messages.push({ role: 'bot', text: data.response });
                } else {
                    activeChat.messages.push({ role: 'bot', text: 'Ошибка: ' + (data.detail || 'Не удалось получить ответ.') });
                }
            } catch (err) {
                document.getElementById('loader-row')?.remove();
                activeChat.messages.push({ role: 'bot', text: 'Ошибка соединения с сервером.' });
            }
            saveState();
        }

        async function openAdminPanel() {
            document.getElementById('admin-modal').classList.remove('hidden');
            let res = await fetch('/api/admin/users');
            let data = await res.json();
            if (res.ok) {
                let html = `<table><tr><th>ID</th><th>Имя</th><th>VIP</th><th>Бан</th><th>Действия</th></tr>`;
                data.users.forEach(u => {
                    html += `<tr>
                        <td>${u.telegram_id}</td>
                        <td>${u.username || 'Нет'}</td>
                        <td>${u.is_vip ? '👑 Да' : 'Нет'}</td>
                        <td>${u.is_banned ? '🔴 Да' : 'Нет'}</td>
                        <td>
                            <button onclick="adminAction(${u.telegram_id}, 'toggle_vip')" style="padding:4px 8px; font-size:11px; width:auto; background:#6366f1; color:#fff; border:none; border-radius:4px; cursor:pointer;">VIP</button>
                            <button onclick="adminAction(${u.telegram_id}, 'toggle_ban')" style="padding:4px 8px; font-size:11px; width:auto; background:#ef4444; color:#fff; border:none; border-radius:4px; cursor:pointer;">Бан</button>
                        </td>
                    </tr>`;
                });
                html += `</table>`;
                document.getElementById('users-table-container').innerHTML = html;
            }
        }

        async function adminAction(userId, action) {
            let formData = new FormData();
            formData.append('telegram_id', userId);
            formData.append('action', action);
            let res = await fetch('/api/admin/action', { method: 'POST', body: formData });
            if (res.ok) openAdminPanel();
        }

        function logout() {
            document.cookie = "rubinov_tg_id=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";
            location.reload();
        }

        function escapeHtml(text) {
            return (text || '').replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_TEMPLATE

def run_telegram_bot():
    asyncio.run(dp.start_polling(bot))

if __name__ == "__main__":
    import uvicorn
    threading.Thread(target=run_telegram_bot, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
