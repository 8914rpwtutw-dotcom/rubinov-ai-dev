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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
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
        keyboard.append([KeyboardButton(text="👑 Админ-панель")])
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
    expires_at = time.time() + 900
    save_auth_code(tg_id, code, expires_at)
    
    await message.answer(
        f"🔑 Ваш код для входа на сайт:\n\n"
        f"<code>{code}</code>\n\n"
        f"⏰ Действителен 15 минут.",
        parse_mode="HTML"
    )

@dp.message(F.text.contains("🎫 Тикеты"))
async def btn_tickets(message: types.Message):
    tg_id = str(message.from_user.id)
    username = message.from_user.username or "user"
    register_user_if_not_exists(tg_id, username)
    
    # Если нажал админ — показываем список тикетов
    if tg_id == str(ADMIN_ID):
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
        return

    # Если обычный пользователь — активируем режим ожидания тикета
    set_waiting_for_ticket(tg_id, 1)
    await message.answer(
        "💬 <b>Создание тикета в поддержку:</b>\n\n"
        "Напишите ваш вопрос или проблему следующим сообщением.",
        parse_mode="HTML"
    )

@dp.message(F.text.contains("Админ-панель"))
async def btn_admin_panel(message: types.Message):
    tg_id = str(message.from_user.id)
    if tg_id != str(ADMIN_ID): return
    await message.answer(
        "👑 <b>Панель администратора:</b>\n"
        "• /ban [ID]\n• /unban [ID]\n• /vip [ID]\n• /unvip [ID]\n• /reply [ID] [текст]",
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
    
    if status["waiting_for_ticket"] == 1:
        save_ticket(tg_id, username, message.text, time.time())
        set_waiting_for_ticket(tg_id, 0)
        await message.answer("✅ Тикет успешно отправлен!")
        if ADMIN_ID:
            try:
                await bot.send_message(int(ADMIN_ID), f"📩 <b>Новый тикет от @{username} ({tg_id}):</b>\n{message.text}\n\nОтветить: <code>/reply {tg_id} текст</code>", parse_mode="HTML")
            except: pass
        return

    await message.answer("ℹ️ Используйте кнопки меню. Чтобы обратиться в поддержку, нажмите <b>🎫 Тикеты</b>.", parse_mode="HTML")


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
    if not bot: return {"status": "error"}
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
    raise HTTPException(status_code=400, detail="Неверный код или истекло время.")

def get_gemini_response(prompt: str, file_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> str:
    global current_key_idx, current_model_idx
    lowered = prompt.lower()
    if "нарисуй" in lowered or "draw" in lowered or "сгенерируй" in lowered:
        clean_prompt = prompt.replace("Нарисуй:", "").replace("нарисуй", "").replace("сгенерируй", "").strip() or "futuristic neon landscape"
        import urllib.parse
        img_url = f"https://image.pollinations.ai/prompt/{urllib.parse.quote(clean_prompt)}?width=1024&height=1024&nologo=true"
        return f'Вот изображение: *"{clean_prompt}"*<div style="margin-top:14px;"><img src="{img_url}" style="max-width:100%; border-radius:16px; display:block; margin-bottom:12px;" /><a href="{img_url}" target="_blank" download="image.jpg" style="background:linear-gradient(135deg, #6366f1, #a855f7); color:#fff; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:600;">📥 Скачать</a></div>'

    api_keys = get_api_keys()
    if not api_keys: raise HTTPException(status_code=500, detail="Ключи не найдены.")
    
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

# ==================== СТАРЫЙ УНИКАЛЬНЫЙ ДИЗАЙН ИНТЕРФЕЙСА ====================
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
        :root {
            --bg-base: #0f1117;
            --bg-sidebar: #161922;
            --bg-chat: #13151c;
            --border-color: rgba(255, 255, 255, 0.08);
            --accent-purple: #8b5cf6;
            --accent-blue: #3b82f6;
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { background: var(--bg-base); color: var(--text-main); height: 100dvh; display: flex; overflow: hidden; }

        /* Оверлей авторизации */
        #auth-overlay {
            position: fixed; inset: 0; background: rgba(10, 11, 15, 0.9); backdrop-filter: blur(12px);
            z-index: 9999; display: flex; align-items: center; justify-content: center;
        }
        .auth-box {
            background: #1a1d28; border: 1px solid rgba(139, 92, 246, 0.3); padding: 32px; border-radius: 20px;
            width: 360px; text-align: center; box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }
        .auth-box h2 { margin-bottom: 12px; font-size: 20px; color: #fff; }
        .auth-box p { font-size: 13px; color: var(--text-muted); margin-bottom: 20px; }
        .auth-box a {
            display: inline-block; background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3);
            padding: 10px 16px; border-radius: 12px; text-decoration: none; font-weight: 600; font-size: 13px; margin-bottom: 20px;
        }
        .auth-box input {
            width: 100%; background: #0f1117; border: 1px solid var(--border-color); color: #fff;
            padding: 12px; border-radius: 12px; font-size: 20px; text-align: center; letter-spacing: 6px; outline: none; margin-bottom: 16px;
        }
        .auth-box button {
            width: 100%; background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple)); border: none;
            color: #fff; padding: 12px; border-radius: 12px; font-weight: 600; cursor: pointer;
        }

        /* Сайдбар */
        aside {
            width: 280px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color);
            display: flex; flex-direction: column; padding: 16px; gap: 16px;
        }
        .brand-area { display: flex; align-items: center; justify-content: space-between; }
        .brand-area h1 { font-size: 16px; font-weight: 700; color: #fff; }
        .vip-tag { background: linear-gradient(135deg, #f59e0b, #d97706); color: #000; font-size: 10px; font-weight: 800; padding: 3px 8px; border-radius: 6px; }
        
        .btn-new {
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple)); border: none; color: #fff;
            padding: 10px; border-radius: 12px; font-weight: 600; font-size: 13px; cursor: pointer; text-align: center;
        }
        .chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
        .chat-row {
            background: rgba(255,255,255,0.02); border: 1px solid transparent; padding: 10px 12px; border-radius: 10px;
            font-size: 13px; color: var(--text-muted); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
        }
        .chat-row.active { background: rgba(139, 92, 246, 0.1); border-color: rgba(139, 92, 246, 0.3); color: #fff; }

        /* Основной блок чата */
        main { flex: 1; display: flex; flex-direction: column; background: var(--bg-chat); position: relative; }
        header {
            height: 60px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center;
            justify-content: space-between; padding: 0 24px; background: rgba(19, 21, 28, 0.7); backdrop-filter: blur(8px);
        }
        header h3 { font-size: 14px; font-weight: 600; color: #fff; }

        .chat-messages {
            flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; max-width: 850px; width: 100%; margin: 0 auto;
        }
        .message { padding: 14px 18px; border-radius: 16px; font-size: 14px; line-height: 1.5; max-width: 85%; }
        .message.user { background: rgba(59, 130, 246, 0.15); border: 1px solid rgba(59, 130, 246, 0.3); align-self: flex-end; border-bottom-right-radius: 4px; }
        .message.bot { background: #1a1d28; border: 1px solid var(--border-color); align-self: flex-start; border-bottom-left-radius: 4px; }

        .input-area { padding: 16px 24px; background: var(--bg-base); border-top: 1px solid var(--border-color); }
        .input-box {
            max-width: 850px; margin: 0 auto; background: #161922; border: 1px solid var(--border-color);
            border-radius: 16px; display: flex; align-items: center; padding: 8px 12px; gap: 10px;
        }
        .input-box input {
            flex: 1; background: transparent; border: none; color: #fff; font-size: 14px; outline: none; padding: 6px;
        }
        .input-box button {
            background: linear-gradient(135deg, var(--accent-blue), var(--accent-purple)); border: none; color: #fff;
            padding: 8px 16px; border-radius: 10px; font-weight: 600; cursor: pointer; font-size: 13px;
        }
    </style>
</head>
<body>

    <div id="auth-overlay" style="display:none;">
        <div class="auth-box">
            <h2>Авторизация</h2>
            <p>Перейдите в Telegram-бота, получите код и введите его ниже:</p>
            <a href="https://t.me/Rubinov_Ai_bot" target="_blank">🤖 Открыть @Rubinov_Ai_bot</a>
            <input type="text" id="code-input" placeholder="000000" maxlength="6">
            <button onclick="auth()">Войти в систему</button>
        </div>
    </div>

    <aside>
        <div class="brand-area">
            <h1>Rubinov AI</h1>
            <div id="vip-slot"></div>
        </div>
        <button class="btn-new" onclick="newChat()">+ Новая беседа</button>
        <div class="chats-list" id="chats-list"></div>
        <button onclick="logout()" style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:12px; text-align:left;">Выйти аккаунта</button>
    </aside>

    <main>
        <header>
            <h3 id="header-title">Диалог</h3>
            <div id="header-status" style="font-size:12px; color:var(--text-muted);"></div>
        </header>

        <div class="chat-messages" id="chat-messages"></div>

        <div class="input-area">
            <div class="input-box">
                <input type="text" id="user-input" placeholder="Введите сообщение..." onkeydown="if(event.key==='Enter') send()">
                <button onclick="send()">Отправить</button>
            </div>
        </div>
    </main>

    <script>
        let tgId = localStorage.getItem('rub_tg') || 'demo';
        let chats = JSON.parse(localStorage.getItem('rub_chats') || '[]');
        let activeId = localStorage.getItem('rub_act') || null;

        if (tgId === 'demo') document.getElementById('auth-overlay').style.display = 'flex';
        else checkStatus();

        async function auth() {
            let code = document.getElementById('code-input').value.trim();
            let fd = new FormData(); fd.append('code', code);
            let res = await fetch('/api/auth/verify-code', { method: 'POST', body: fd });
            let data = await res.json();
            if (res.ok) {
                tgId = data.telegram_id;
                localStorage.setItem('rub_tg', tgId);
                document.getElementById('auth-overlay').style.display = 'none';
                checkStatus();
                init();
            } else { alert(data.detail || 'Неверный код'); }
        }

        async function checkStatus() {
            let res = await fetch(`/api/user/status?telegram_id=${tgId}`);
            if (!res.ok) { document.getElementById('auth-overlay').style.display = 'flex'; return; }
            let data = await res.json();
            if (data.is_banned) { alert('Вы забанены'); return; }
            if (data.is_vip) {
                document.getElementById('vip-slot').innerHTML = '<span class="vip-tag">VIP</span>';
                document.getElementById('header-status').textContent = 'VIP Режим активен';
            }
            init();
        }

        function logout() { localStorage.removeItem('rub_tg'); location.reload(); }

        function init() {
            if (chats.length === 0) newChat();
            render();
        }

        function newChat() {
            let chat = { id: Date.now().toString(), title: 'Новый диалог', msgs: [] };
            chats.push(chat);
            activeId = chat.id;
            saveAndRender();
        }

        function saveAndRender() {
            localStorage.setItem('rub_chats', JSON.stringify(chats));
            localStorage.setItem('rub_act', activeId);
            render();
        }

        function render() {
            let list = document.getElementById('chats-list');
            list.innerHTML = '';
            chats.forEach(c => {
                let div = document.createElement('div');
                div.className = `chat-row ${c.id === activeId ? 'active' : ''}`;
                div.textContent = c.title;
                div.onclick = () => { activeId = c.id; saveAndRender(); };
                list.appendChild(div);
            });

            let cur = chats.find(c => c.id === activeId);
            if (cur) {
                document.getElementById('header-title').textContent = cur.title;
                let box = document.getElementById('chat-messages');
                box.innerHTML = cur.msgs.map(m => `<div class="message ${m.role}">${marked.parse(m.text)}</div>`).join('');
                box.scrollTop = box.scrollHeight;
            }
        }

        async function send() {
            let input = document.getElementById('user-input');
            let text = input.value.trim();
            if (!text) return;
            let cur = chats.find(c => c.id === activeId);
            if (!cur) return;

            cur.msgs.push({ role: 'user', text });
            if (cur.msgs.length === 1) cur.title = text.slice(0, 18);
            input.value = '';
            saveAndRender();

            let fd = new FormData();
            fd.append('prompt', text);
            fd.append('telegram_id', tgId);

            let res = await fetch('/api/chat', { method: 'POST', body: fd });
            let data = await res.json();
            cur.msgs.push({ role: 'bot', text: res.ok ? data.response : 'Ошибка сервера' });
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
