import os
import time
import random
import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types
from google.genai.errors import APIError

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import httpx

# ==================== БАЗА ДАННЫХ ====================
DB_FILE = "database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            is_banned INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            auth_code TEXT,
            auth_code_expires REAL,
            waiting_for_ticket INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT,
            username TEXT,
            message TEXT,
            created_at REAL
        )
    """)
    conn.commit()
    conn.close()

def register_user_if_not_exists(telegram_id: str, username: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO users (telegram_id, username, is_banned, is_vip, waiting_for_ticket) VALUES (?, ?, 0, 0, 0)", (telegram_id, username))
        conn.commit()
    conn.close()

def check_user_status(telegram_id: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT is_banned, is_vip, waiting_for_ticket FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"is_banned": False, "is_vip": False, "waiting_for_ticket": 0}
    return {"is_banned": bool(row[0]), "is_vip": bool(row[1]), "waiting_for_ticket": row[2]}

def set_waiting_for_ticket(telegram_id: str, state: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET waiting_for_ticket = ? WHERE telegram_id = ?", (state, telegram_id))
    conn.commit()
    conn.close()

def save_auth_code(telegram_id: str, code: str, expires_at: float):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET auth_code = ?, auth_code_expires = ? WHERE telegram_id = ?", (code, expires_at, telegram_id))
    conn.commit()
    conn.close()

def find_user_by_auth_code(code: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, auth_code_expires FROM users WHERE auth_code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    telegram_id, expires_at = row
    if time.time() > expires_at:
        return None
    return telegram_id

def set_user_ban(telegram_id: str, banned: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_banned = ? WHERE telegram_id = ?", (banned, telegram_id))
    conn.commit()
    conn.close()

def set_user_vip(telegram_id: str, vip: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_vip = ? WHERE telegram_id = ?", (vip, telegram_id))
    conn.commit()
    conn.close()

def save_ticket(telegram_id: str, username: str, message: str, created_at: float):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO tickets (telegram_id, username, message, created_at) VALUES (?, ?, ?, ?)", (telegram_id, username, message, created_at))
    conn.commit()
    conn.close()

def get_all_tickets():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, telegram_id, username, message, created_at FROM tickets ORDER BY id DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()
    return rows


# ==================== TELEGRAM БОТ ====================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID", "")

bot = Bot(token=BOT_TOKEN) if BOT_TOKEN else None
dp = Dispatcher()

def get_main_keyboard(is_admin: bool = False):
    keyboard = [
        [KeyboardButton(text="🔑 Получить код"), KeyboardButton(text="🎫 Тикеты")],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="👑 Админ-панель"), KeyboardButton(text="📋 Просмотр тикетов")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    init_db()
    tg_id = str(message.from_user.id)
    username = message.from_user.username or "user"
    register_user_if_not_exists(tg_id, username)
    set_waiting_for_ticket(tg_id, 0)
    is_admin = (tg_id == str(ADMIN_ID))
    
    await message.answer(
        f"👋 Привет, <b>@{username}</b>!\n\n"
        f"🤖 Добро пожаловать в <b>Rubinov AI</b>.\n"
        f"Используйте кнопки ниже для управления.",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(is_admin)
    )

@dp.message(F.text.contains("Получить код"))
async def btn_get_code(message: types.Message):
    tg_id = str(message.from_user.id)
    username = message.from_user.username or "user"
    register_user_if_not_exists(tg_id, username)
    set_waiting_for_ticket(tg_id, 0)
    
    status = check_user_status(tg_id)
    if status["is_banned"]:
        await message.answer("⛔ Вы забанены.")
        return

    code = str(random.randint(100000, 999900))
    expires_at = time.time() + 900  # 15 минут
    save_auth_code(tg_id, code, expires_at)
    
    await message.answer(
        f"🔑 Ваш код для входа на сайт:\n\n"
        f"<code>{code}</code>\n\n"
        f"⏰ Действителен 15 минут. Введите его на сайте.",
        parse_mode="HTML"
    )

@dp.message(F.text.contains("🎫 Тикеты"))
async def btn_tickets(message: types.Message):
    tg_id = str(message.from_user.id)
    username = message.from_user.username or "user"
    register_user_if_not_exists(tg_id, username)
    
    # Включаем режим ожидания текста тикета
    set_waiting_for_ticket(tg_id, 1)
    await message.answer(
        "💬 <b>Создание тикета в поддержку:</b>\n\n"
        "Опишите ваш вопрос или проблему прямо следующим сообщением, и оно будет отправлено администратору.",
        parse_mode="HTML"
    )

@dp.message(F.text.contains("📋 Просмотр тикетов"))
async def btn_view_tickets(message: types.Message):
    tg_id = str(message.from_user.id)
    if tg_id != str(ADMIN_ID):
        return
    
    tickets = get_all_tickets()
    if not tickets:
        await message.answer("📭 Активных тикетов нет.")
        return
    
    text = "📋 <b>Последние тикеты:</b>\n\n"
    for t_id, t_tg_id, t_username, t_msg, t_time in tickets:
        time_str = time.strftime('%d.%m %H:%M', time.localtime(t_time))
        text += f"🆔 <b>Тикет #{t_id}</b> от @{t_username} (<code>{t_tg_id}</code>) от {time_str}:\n" \
                f"💬 {t_msg}\n" \
                f"Ответить: <code>/reply {t_tg_id} текст</code>\n" \
                f"-----------------------------------\n"
    
    await message.answer(text, parse_mode="HTML")

@dp.message(F.text.contains("Админ-панель"))
async def btn_admin_panel(message: types.Message):
    tg_id = str(message.from_user.id)
    if tg_id != str(ADMIN_ID):
        return
    await message.answer(
        "👑 <b>Панель администратора:</b>\n\n"
        "Команды:\n"
        "• /ban [ID]\n"
        "• /unban [ID]\n"
        "• /vip [ID]\n"
        "• /unvip [ID]\n"
        "• /reply [ID] [текст]",
        parse_mode="HTML"
    )

@dp.message(Command("ban"))
async def cmd_ban(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    args = message.text.split()
    if len(args) < 2: return
    set_user_ban(args[1], 1)
    await bot.send_message(int(args[1]), "❌ Вы забанены.")
    await message.answer(f"✅ Забанен {args[1]}")

@dp.message(Command("unban"))
async def cmd_unban(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    args = message.text.split()
    if len(args) < 2: return
    set_user_ban(args[1], 0)
    await bot.send_message(int(args[1]), "✅ Вы разбанены.")
    await message.answer(f"✅ Разбанен {args[1]}")

@dp.message(Command("vip"))
async def cmd_vip(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    args = message.text.split()
    if len(args) < 2: return
    set_user_vip(args[1], 1)
    await bot.send_message(int(args[1]), "⭐ Вам выдан VIP!")
    await message.answer(f"⭐ VIP выдан {args[1]}")

@dp.message(Command("unvip"))
async def cmd_unvip(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    args = message.text.split()
    if len(args) < 2: return
    set_user_vip(args[1], 0)
    await bot.send_message(int(args[1]), "ℹ️ VIP снят.")
    await message.answer(f"ℹ️ VIP снят с {args[1]}")

@dp.message(Command("reply"))
async def cmd_reply(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    parts = message.text.split(" ", 2)
    if len(parts) < 3: return
    try:
        await bot.send_message(int(parts[1]), f"💬 <b>Ответ поддержки:</b>\n{parts[2]}", parse_mode="HTML")
        await message.answer("✅ Ответ успешно отправлен пользователю.")
    except Exception as e:
        await message.answer(f"❌ Ошибка отправки: {e}")

@dp.message()
async def handle_other_messages(message: types.Message):
    if not message.text or message.text.startswith("/"): return
    tg_id = str(message.from_user.id)
    username = message.from_user.username or "user"
    status = check_user_status(tg_id)
    
    if status["is_banned"]:
        await message.answer("⛔ Вы забанены.")
        return
    
    # Проверяем, ждем ли мы от пользователя текст тикета
    if status["waiting_for_ticket"] == 1:
        save_ticket(tg_id, username, message.text, time.time())
        set_waiting_for_ticket(tg_id, 0) # Сбрасываем режим
        
        await message.answer("✅ Ваш тикет успешно отправлен в поддержку! Ожидайте ответа.")
        
        if ADMIN_ID:
            try:
                await bot.send_message(
                    int(ADMIN_ID), 
                    f"📩 <b>Новый тикет от @{username} ({tg_id}):</b>\n{message.text}\n\nОтветить: <code>/reply {tg_id} текст</code>", 
                    parse_mode="HTML"
                )
            except: pass
        return

    # Обычные сообщения (если не нажата кнопка тикета) просто игнорируются или выводят подсказку
    await message.answer("ℹ️ Используйте кнопки меню. Чтобы обратиться в поддержку, нажмите кнопку <b>🎫 Тикеты</b>.", parse_mode="HTML")


# ==================== FASTAPI СЕРВЕР ====================
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

init_db()

MODELS = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-pro"]
current_key_idx = 0
current_model_idx = 0

def get_api_keys():
    keys = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2"), os.getenv("GEMINI_KEY_3"), os.getenv("GEMINI_API_KEY")]
    return [k.strip() for k in keys if k and k.strip()]

@app.on_event("startup")
async def startup_event():
    render_url = os.getenv("RENDER_EXTERNAL_URL")
    if render_url and BOT_TOKEN:
        webhook_url = f"{render_url}/telegram-webhook"
        async with httpx.AsyncClient() as client:
            await client.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook?url={webhook_url}")

@app.post("/telegram-webhook")
async def telegram_webhook(update: dict):
    if not bot:
        return {"status": "error"}
    await dp.feed_update(bot, types.Update(**update))
    return {"status": "ok"}

@app.get("/api/user/status")
def api_get_status(telegram_id: str):
    return check_user_status(telegram_id)

@app.post("/api/auth/verify-code")
def verify_code(code: str = Form(...)):
    clean_code = code.strip()
    telegram_id = find_user_by_auth_code(clean_code)
    if telegram_id:
        save_auth_code(telegram_id, "", 0)
        return {"status": "success", "telegram_id": telegram_id, "user": check_user_status(telegram_id)}
    raise HTTPException(status_code=400, detail="Неверный код или истекло время (15 минут).")

def get_gemini_response(prompt: str, file_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> str:
    global current_key_idx, current_model_idx
    lowered = prompt.lower()
    if "нарисуй" in lowered or "draw" in lowered or "сгенерируй" in lowered:
        clean_prompt = prompt.replace("Нарисуй:", "").replace("нарисуй", "").replace("сгенерируй", "").strip() or "futuristic neon landscape"
        import urllib.parse
        img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(clean_prompt)}?width=1024&height=1024&nologo=true"
        return f'Вот изображение: *"{clean_prompt}"*<div style="margin-top:14px;"><img src="{img_url}" style="max-width:100%; border-radius:16px; display:block; margin-bottom:12px;" /><a href="{img_url}" target="_blank" download="image.jpg" style="background:linear-gradient(135deg, #6366f1, #a855f7); color:#fff; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:600;">📥 Скачать</a></div>'

    api_keys = get_api_keys()
    if not api_keys: raise HTTPException(status_code=500, detail="API-ключи не найдены.")
    
    contents = []
    if file_bytes and mime_type: contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))
    if prompt: contents.append(prompt)

    for _ in range(len(api_keys) * len(MODELS)):
        try:
            client = genai.Client(api_key=api_keys[current_key_idx % len(api_keys)])
            res = client.models.generate_content(model=MODELS[current_model_idx % len(MODELS)], contents=contents)
            return res.text
        except APIError as e:
            if e.code in [503, 429] or "RESOURCE_EXHAUSTED" in str(e):
                current_model_idx = (current_model_idx + 1) % len(MODELS)
                time.sleep(0.3)
                continue
            break
        except: break
    raise HTTPException(status_code=500, detail="ИИ перегружен.")

@app.post("/api/chat")
async def chat_endpoint(prompt: str = Form(""), file: Optional[UploadFile] = File(None), telegram_id: str = Form("demo_user")):
    if telegram_id == "demo_user": raise HTTPException(status_code=401, detail="Требуется авторизация.")
    status = check_user_status(telegram_id)
    if status["is_banned"]: raise HTTPException(status_code=403, detail="Забанен.")
    if file and not status["is_vip"]: raise HTTPException(status_code=403, detail="Только для VIP.")
    if not prompt.strip() and not file: raise HTTPException(status_code=400, detail="Пустой запрос.")
    
    file_bytes = await file.read() if file else None
    mime_type = file.content_type if file else None
    return {"response": get_gemini_response(prompt, file_bytes, mime_type)}

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rubinov AI</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root { --bg-main: #040508; --bg-sidebar: rgba(10, 12, 18, 0.85); --border-color: rgba(255, 255, 255, 0.06); --accent-gradient: linear-gradient(135deg, #6366f1 0%, #a855f7 100%); --vip-gradient: linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%); --text-main: #f1f5f9; --text-muted: #94a3b8; }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); display: flex; }
        #auth-overlay { position: fixed; top: 0; left: 0; width: 100vw; height: 100dvh; background: rgba(4, 5, 8, 0.9); backdrop-filter: blur(20px); z-index: 10000; display: flex; align-items: center; justify-content: center; padding: 20px; }
        .auth-modal { background: rgba(15, 18, 26, 0.95); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: 24px; padding: 32px; width: 100%; max-width: 400px; text-align: center; display: flex; flex-direction: column; gap: 16px; }
        .auth-modal input { background: rgba(0, 0, 0, 0.5); border: 1px solid var(--border-color); border-radius: 12px; color: #fff; padding: 12px; font-size: 18px; text-align: center; letter-spacing: 4px; font-weight: 700; outline: none; }
        .auth-modal button { background: var(--accent-gradient); color: #fff; border: none; border-radius: 12px; padding: 12px; font-weight: 600; cursor: pointer; }
        .btn-bot-link { background: rgba(56, 189, 248, 0.1); border: 1px solid rgba(56, 189, 248, 0.3); color: #38bdf8; text-decoration: none; border-radius: 12px; padding: 10px; font-size: 13px; font-weight: 600; display: inline-flex; align-items: center; justify-content: center; gap: 8px; }
        .vip-badge { background: var(--vip-gradient); color: #000; font-size: 10px; font-weight: 800; padding: 4px 8px; border-radius: 6px; text-transform: uppercase; }
        #sidebar { width: 290px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; padding: 18px 14px; height: 100dvh; z-index: 50; }
        .brand { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
        .brand h2 { font-size: 14px; font-weight: 700; color: #fff; }
        .btn-new-chat { background: var(--accent-gradient); color: #fff; border: none; padding: 10px; border-radius: 12px; font-size: 12px; font-weight: 600; cursor: pointer; margin-bottom: 14px; }
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
        .chat-item { background: rgba(255,255,255,0.015); border: 1px solid var(--border-color); border-radius: 10px; padding: 10px; font-size: 12px; color: #cbd5e1; display: flex; justify-content: space-between; cursor: pointer; }
        .chat-item.active { background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.35); color: #fff; }
        #main { flex: 1; display: flex; flex-direction: column; position: relative; height: 100dvh; }
        #chat-header { height: 60px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center; justify-content: space-between; padding: 0 24px; background: rgba(4,5,8,0.5); }
        #chat-container { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; max-width: 900px; width: 100%; margin: 0 auto; }
        .msg-user { background: rgba(99, 102, 241, 0.16); border: 1px solid rgba(168, 85, 247, 0.22); border-radius: 18px 18px 4px 18px; padding: 12px 16px; max-width: 85%; align-self: flex-end; font-size: 13.5px; }
        .msg-bot { background: rgba(15, 18, 26, 0.65); border: 1px solid var(--border-color); border-radius: 18px 18px 18px 4px; padding: 16px; max-width: 90%; align-self: flex-start; font-size: 13.5px; }
        #input-wrapper { padding: 16px 24px; background: var(--bg-main); }
        #input-container { max-width: 900px; margin: 0 auto; background: rgba(13, 16, 24, 0.8); border: 1px solid rgba(168, 85, 247, 0.18); border-radius: 20px; padding: 8px 12px; display: flex; gap: 8px; align-items: center; }
        #prompt-input { flex: 1; background: transparent; border: none; color: #fff; font-size: 14px; outline: none; padding: 6px; }
        .btn-action { background: var(--accent-gradient); color: #fff; border: none; border-radius: 12px; padding: 10px 18px; font-weight: 600; cursor: pointer; }
    </style>
</head>
<body>
    <div id="auth-overlay" style="display:none;">
        <div class="auth-modal">
            <h2>Авторизация</h2>
            <p style="font-size:13px; color:var(--text-muted);">Откройте бота, получите код и введите его:</p>
            <a href="https://t.me/Rubinov_Ai_bot" target="_blank" class="btn-bot-link">Открыть Telegram-бота</a>
            <input type="text" id="otp-input" placeholder="000000" maxlength="6" />
            <button onclick="verifyOtpCode()">Войти</button>
        </div>
    </div>

    <div id="sidebar">
        <div class="brand">
            <h2>Rubinov AI</h2>
            <div id="badge-container"></div>
        </div>
        <button class="btn-new-chat" onclick="createNewChat()">+ Новый диалог</button>
        <div id="chats-list"></div>
        <button onclick="logout()" style="margin-top:auto; background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:11px; text-align:left;">Выйти</button>
    </div>

    <div id="main">
        <div id="chat-header">
            <h3 id="current-chat-title">Чат</h3>
            <div id="header-vip"></div>
        </div>
        <div id="chat-container"></div>
        <div id="input-wrapper">
            <div id="input-container">
                <input type="text" id="prompt-input" placeholder="Введите сообщение..." onkeydown="if(event.key==='Enter') sendMessage()" />
                <button class="btn-action" onclick="sendMessage()">Отправить</button>
            </div>
        </div>
    </div>

    <script>
        let tgId = localStorage.getItem('rubinov_tg_id') || 'demo_user';
        let chats = JSON.parse(localStorage.getItem('rubinov_chats') || '[]');
        let currentChatId = localStorage.getItem('rubinov_active') || null;

        if (tgId === 'demo_user') document.getElementById('auth-overlay').style.display = 'flex';
        else checkStatus();

        async function verifyOtpCode() {
            const code = document.getElementById('otp-input').value.trim();
            const formData = new FormData(); formData.append('code', code);
            const res = await fetch('/api/auth/verify-code', { method: 'POST', body: formData });
            const data = await res.json();
            if (res.ok) {
                tgId = data.telegram_id;
                localStorage.setItem('rubinov_tg_id', tgId);
                document.getElementById('auth-overlay').style.display = 'none';
                checkStatus();
                initChats();
            } else { alert(data.detail || 'Ошибка'); }
        }

        async function checkStatus() {
            const res = await fetch(`/api/user/status?telegram_id=${tgId}`);
            if (!res.ok) { document.getElementById('auth-overlay').style.display = 'flex'; return; }
            const data = await res.json();
            if (data.is_banned) { alert('Вы забанены'); return; }
            if (data.is_vip) {
                document.getElementById('badge-container').innerHTML = '<span class="vip-badge">VIP</span>';
                document.getElementById('header-vip').innerHTML = '<span class="vip-badge">VIP Активен</span>';
            }
            initChats();
        }

        function logout() { localStorage.removeItem('rubinov_tg_id'); location.reload(); }

        function initChats() {
            if (chats.length === 0) createNewChat();
            renderChats();
        }

        function createNewChat() {
            const newChat = { id: Date.now().toString(), name: 'Новый чат', messages: [] };
            chats.push(newChat);
            currentChatId = newChat.id;
            saveAndRender();
        }

        function saveAndRender() {
            localStorage.setItem('rubinov_chats', JSON.stringify(chats));
            localStorage.setItem('rubinov_active', currentChatId);
            renderChats();
        }

        function renderChats() {
            const list = document.getElementById('chats-list');
            list.innerHTML = '';
            chats.forEach(c => {
                const div = document.createElement('div');
                div.className = `chat-item ${c.id === currentChatId ? 'active' : ''}`;
                div.textContent = c.name;
                div.onclick = () => { currentChatId = c.id; saveAndRender(); };
                list.appendChild(div);
            });
            const active = chats.find(c => c.id === currentChatId);
            if (active) {
                document.getElementById('current-chat-title').textContent = active.name;
                const container = document.getElementById('chat-container');
                container.innerHTML = active.messages.map(m => `<div class="${m.role === 'user' ? 'msg-user' : 'msg-bot'}">${marked.parse(m.text)}</div>`).join('');
                container.scrollTop = container.scrollHeight;
            }
        }

        async function sendMessage() {
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text) return;
            const active = chats.find(c => c.id === currentChatId);
            if (!active) return;

            active.messages.push({ role: 'user', text });
            if (active.messages.length === 1) active.name = text.slice(0, 15);
            input.value = '';
            saveAndRender();

            const formData = new FormData();
            formData.append('prompt', text);
            formData.append('telegram_id', tgId);

            const res = await fetch('/api/chat', { method: 'POST', body: formData });
            const data = await res.json();
            active.messages.push({ role: 'bot', text: res.ok ? data.response : 'Ошибка сервера' });
            saveAndRender();
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_ui():
    return HTML_TEMPLATE

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
