import os
import time
import random
import sqlite3
import asyncio
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
    from aiogram import Bot, Dispatcher, F, types as aiogram_types
    from aiogram.filters import Command
    from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
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
ADMIN_TELEGRAM_ID = str(os.getenv("ADMIN_TELEGRAM_ID", ""))

security = HTTPBearer()
DB_NAME = "rubinov_secure.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            tier TEXT DEFAULT 'free',
            is_banned INTEGER DEFAULT 0
        )
    ''')
    # Добавим колонку is_banned на случай, если таблица уже существовала без неё
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass # Колонка уже есть

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS login_codes (
            code TEXT PRIMARY KEY,
            telegram_id TEXT,
            expires_at REAL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            telegram_id TEXT,
            session_id TEXT PRIMARY KEY
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT,
            username TEXT,
            message TEXT,
            created_at REAL
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        telegram_id = payload.get("telegram_id")
        
        # Проверяем не забанен ли пользователь при запросах к API
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0] == 1:
            raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован администратором.")
            
        return telegram_id
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Недействительный токен авторизации.")

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
    
    # Проверка на бан
    cursor.execute("SELECT tier, is_banned FROM users WHERE telegram_id = ?", (telegram_id,))
    user_row = cursor.fetchone()
    if user_row and user_row[1] == 1:
        conn.close()
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован администратором.")
        
    tier = user_row[0] if user_row else 'free'
    
    if time.time() > expires_at:
        cursor.execute("DELETE FROM login_codes WHERE code = ?", (code,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Срок действия кода истек.")
    
    cursor.execute("SELECT COUNT(*) FROM user_sessions WHERE telegram_id = ?", (telegram_id,))
    active_sessions_count = cursor.fetchone()[0]
    
    max_devices = 3 if tier == 'vip' else 1
    if active_sessions_count >= max_devices:
        conn.close()
        raise HTTPException(status_code=403, detail=f"Превышен лимит устройств ({max_devices} для вашего тарифа).")
    
    cursor.execute("DELETE FROM login_codes WHERE code = ?", (code,))
    session_id = str(random.randint(10000000, 99999999))
    cursor.execute("INSERT INTO user_sessions (telegram_id, session_id) VALUES (?, ?)", (telegram_id, session_id))
    
    conn.commit()
    conn.close()
    
    token = jwt.encode({"telegram_id": telegram_id, "session_id": session_id}, JWT_SECRET, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}

# --- ЛОГИКА ИИ ---
MODELS = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-pro"]
current_key_idx = 0
current_model_idx = 0

def get_api_keys():
    keys = [os.getenv("GEMINI_KEY_1"), os.getenv("GEMINI_KEY_2"), os.getenv("GEMINI_KEY_3"), os.getenv("GEMINI_API_KEY")]
    return [k.strip() for k in keys if k and k.strip()]

def get_gemini_response(prompt: str, file_bytes: Optional[bytes] = None, mime_type: Optional[str] = None) -> str:
    global current_key_idx, current_model_idx
    lowered = prompt.lower()
    
    if "нарисуй" in lowered or "draw" in lowered or "сгенерируй" in lowered:
        clean_prompt = prompt.replace("Нарисуй:", "").replace("нарисуй", "").replace("сгенерируй", "").strip() or "cyberpunk landscape"
        encoded_prompt = urllib.parse.quote(clean_prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        return f"""Вот ваше сгенерированное изображение: *"{clean_prompt}"*

<div style="margin-top:14px;">
    <img src="{img_url}" alt="{clean_prompt}" style="max-width:100%; border-radius:16px; display:block; margin-bottom:12px;" />
    <a href="{img_url}" target="_blank" download="image.jpg" style="display:inline-flex; align-items: center; gap: 8px; background: linear-gradient(135deg, #6366f1, #a855f7); color:#fff; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:600;">📥 Скачать</a>
</div>"""

    api_keys = get_api_keys()
    if not api_keys:
        raise HTTPException(status_code=500, detail="API-ключи не найдены.")

    num_keys, num_models = len(api_keys), len(MODELS)
    contents = []
    if file_bytes and mime_type:
        contents.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))
    if prompt:
        contents.append(prompt)

    for _ in range(num_keys * num_models * 2):
        active_key = api_keys[current_key_idx % num_keys]
        active_model = MODELS[current_model_idx % num_models]
        try:
            client = genai.Client(api_key=active_key)
            response = client.models.generate_content(model=active_model, contents=contents)
            return response.text
        except Exception:
            current_model_idx = (current_model_idx + 1) % num_models
            continue

    raise HTTPException(status_code=500, detail="Сервис ИИ перегружен.")

@app.post("/api/chat")
async def chat_endpoint(prompt: str = Form(""), file: Optional[UploadFile] = File(None), telegram_id: str = Depends(get_current_user)):
    file_bytes = await file.read() if file else None
    mime_type = file.content_type if file else None
    answer = get_gemini_response(prompt, file_bytes, mime_type)
    return {"response": answer}

# --- ФРОНТЕНД САЙТА ---
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
        :root { --bg-main: #040508; --accent: linear-gradient(135deg, #6366f1, #a855f7); }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', sans-serif; }
        body { height: 100dvh; background: var(--bg-main); color: #f1f5f9; display: flex; }
        #auth-modal { position: fixed; inset: 0; background: rgba(4,5,8,0.95); z-index: 1000; display: flex; align-items: center; justify-content: center; opacity: 0; pointer-events: none; transition: 0.3s; }
        #auth-modal.active { opacity: 1; pointer-events: auto; }
        .auth-card { background: #12151f; border: 1px solid rgba(168,85,247,0.3); border-radius: 24px; padding: 32px; width: 90%; max-width: 400px; text-align: center; }
        .auth-input { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 12px; color: #fff; font-size: 18px; width: 100%; text-align: center; letter-spacing: 4px; margin: 16px 0; outline: none; }
        .auth-btn { background: var(--accent); color: #fff; border: none; border-radius: 14px; padding: 12px; font-weight: 600; width: 100%; cursor: pointer; }
        #main { flex: 1; display: flex; flex-direction: column; height: 100dvh; }
        #chat-container { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; max-width: 800px; width: 100%; margin: 0 auto; }
        .msg { padding: 14px 18px; border-radius: 16px; max-width: 85%; font-size: 14px; line-height: 1.5; }
        .user-msg { background: rgba(99,102,241,0.2); align-self: flex-end; }
        .bot-msg { background: #0f121a; border: 1px solid rgba(255,255,255,0.05); align-self: flex-start; }
        #input-box { padding: 20px; max-width: 800px; width: 100%; margin: 0 auto; display: flex; gap: 10px; }
        #prompt-input { flex: 1; background: #0d1018; border: 1px solid rgba(255,255,255,0.1); border-radius: 14px; padding: 12px; color: #fff; outline: none; }
        .send-btn { background: var(--accent); color: #fff; border: none; padding: 0 20px; border-radius: 14px; font-weight: 600; cursor: pointer; }
    </style>
</head>
<body>
    <div id="auth-modal" class="active">
        <div class="auth-card">
            <h2>Авторизация</h2>
            <p style="font-size: 13px; color: #94a3b8; margin-top: 8px;">Запросите код в нашем Telegram-боте</p>
            <input type="text" id="code-input" class="auth-input" placeholder="000000" maxlength="6">
            <button class="auth-btn" onclick="submitCode()">Войти</button>
        </div>
    </div>
    <div id="main">
        <div id="chat-container"></div>
        <div id="input-box">
            <input type="text" id="prompt-input" placeholder="Введите сообщение или попросите нарисовать..." onkeydown="if(event.key==='Enter')sendMessage()">
            <button class="send-btn" onclick="sendMessage()">Отправить</button>
        </div>
    </div>
    <script>
        let token = localStorage.getItem('token');
        if (token) document.getElementById('auth-modal').classList.remove('active');

        async function submitCode() {
            const code = document.getElementById('code-input').value.trim();
            const res = await fetch('/api/auth/verify-code', { method: 'POST', body: new URLSearchParams({ code }) });
            const data = await res.json();
            if (res.ok) { localStorage.setItem('token', data.access_token); location.reload(); }
            else alert(data.detail);
        }

        async function sendMessage() {
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text) return;
            
            const container = document.getElementById('chat-container');
            container.innerHTML += `<div class="msg user-msg">${text}</div>`;
            input.value = '';

            const res = await fetch('/api/chat', { method: 'POST', headers: { 'Authorization': 'Bearer ' + token }, body: new URLSearchParams({ prompt: text }) });
            const data = await res.json();
            if (res.ok) {
                container.innerHTML += `<div class="msg bot-msg">${marked.parse(data.response)}</div>`;
            } else {
                container.innerHTML += `<div class="msg bot-msg" style="color:#f87171">Ошибка: ${data.detail}</div>`;
            }
            container.scrollTop = container.scrollHeight;
        }
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def get_root():
    return HTML_TEMPLATE


# --- TELEGRAM БОТ ---
async def start_telegram_bot():
    if not TELEGRAM_BOT_TOKEN:
        return
        
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    admin_reply_targets = {}
    waiting_for_support = set()
    waiting_for_user_search = set()

    def get_main_keyboard(is_admin: bool):
        builder = ReplyKeyboardBuilder()
        builder.button(text="🔐 Получить код")
        if is_admin:
            builder.button(text="👥 Пользователи")
            builder.button(text="🔍 Найти по ID")
            builder.button(text="📬 Тикеты")
            builder.button(text="👑 Админ-панель")
            builder.adjust(2, 2, 1)
        else:
            builder.button(text="🆘 Поддержка")
            builder.adjust(2)
        return builder.as_markup(resize_keyboard=True)

    @dp.message(Command("start"))
    async def cmd_start(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        uname = m.from_user.username or m.from_user.first_name
        is_admin = (t_id == ADMIN_TELEGRAM_ID)
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username, tier, is_banned) VALUES (?, ?, 'free', 0)", (t_id, uname))
        
        # Проверим бан
        cursor.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (t_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0] == 1 and not is_admin:
            await m.answer("❌ Ваш аккаунт заблокирован администратором. Напишите в поддержку, если считаете, что это ошибка.")
            return

        if is_admin:
            await m.answer(
                "👑 **Панель Администратора активирована**\n\n"
                "Используйте кнопки ниже для быстрого доступа к управлению:",
                reply_markup=get_main_keyboard(True),
                parse_mode="Markdown"
            )
        else:
            await m.answer(
                "👋 Добро пожаловать в **Rubinov AI**!\n\n"
                "Используйте кнопки на клавиатуре снизу для получения кода или связи с поддержкой.",
                reply_markup=get_main_keyboard(False),
                parse_mode="Markdown"
            )

    @dp.message(F.text == "🔐 Получить код")
    async def btn_login(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        uname = m.from_user.username or m.from_user.first_name
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Проверка на бан
        cursor.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (t_id,))
        row = cursor.fetchone()
        if row and row[0] == 1 and t_id != ADMIN_TELEGRAM_ID:
            conn.close()
            await m.answer("❌ Вы забанены администратором. Получение кодов недоступно.")
            return

        cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username, tier, is_banned) VALUES (?, ?, 'free', 0)", (t_id, uname))
        conn.commit()
        
        code = str(random.randint(100000, 999999))
        cursor.execute("INSERT OR REPLACE INTO login_codes (code, telegram_id, expires_at) VALUES (?, ?, ?)", (code, t_id, time.time() + 60))
        conn.commit()
        conn.close()
        
        await m.answer(f"🔐 Ваш код для входа на сайт:\n\n`{code}`\n\n⚠️ *Действителен 1 минуту.*", parse_mode="Markdown")

    @dp.message(F.text == "🆘 Поддержка")
    async def btn_support_prompt(m: aiogram_types.Message):
        waiting_for_support.add(m.from_user.id)
        await m.answer("💬 Опишите вашу проблему или задайте вопрос одним сообщением, и мы передадим его администратору:")

    @dp.message(F.text == "👑 Админ-панель")
    async def btn_admin_panel(m: aiogram_types.Message):
        if str(m.from_user.id) != ADMIN_TELEGRAM_ID:
            return
        await m.answer(
            "👑 **Административная панель управления**\n\n"
            "• **👥 Пользователи** — список всех пользователей, управление VIP и банами.\n"
            "• **🔍 Найти по ID** — быстро найти пользователя по его Telegram ID.\n"
            "• **📬 Тикеты** — проверка обращений в поддержку.",
            reply_markup=get_main_keyboard(True),
            parse_mode="Markdown"
        )

    @dp.message(F.text == "🔍 Найти по ID")
    async def btn_search_user_prompt(m: aiogram_types.Message):
        if str(m.from_user.id) != ADMIN_TELEGRAM_ID:
            return
        waiting_for_user_search.add(m.from_user.id)
        await m.answer("🔍 Введите Telegram ID пользователя, которого хотите найти:", reply_markup=get_main_keyboard(True))

    @dp.message(F.text == "📬 Тикеты")
    async def btn_tickets(m: aiogram_types.Message):
        if str(m.from_user.id) != ADMIN_TELEGRAM_ID:
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, telegram_id, username, message, created_at FROM support_tickets ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await m.answer("📭 Активных запросов в поддержку нет.")
            return

        await m.answer(f"📬 **Активные обращения ({len(rows)}):**", parse_mode="Markdown")

        for r in rows:
            ticket_id, t_id, uname, msg, created = r[0], r[1], r[2], r[3], r[4]
            time_str = time.strftime('%d.%m %H:%M', time.localtime(created))
            
            builder = InlineKeyboardBuilder()
            builder.button(text="✍️ Ответить", callback_data=f"ans_{t_id}")
            builder.button(text="🗑️ Удалить тикет", callback_data=f"delticket_{ticket_id}")
            builder.adjust(1)

            card_text = (
                f"👤 <b>{uname}</b> (ID: <code>{t_id}</code>)\n"
                f"🕒 Время: {time_str}\n"
                f"💬 <b>Вопрос:</b> {msg}"
            )
            await m.answer(card_text, reply_markup=builder.as_markup(), parse_mode="HTML")

    @dp.message(F.text == "👥 Пользователи")
    async def btn_admin_users(m: aiogram_types.Message):
        if str(m.from_user.id) != ADMIN_TELEGRAM_ID:
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username, tier, is_banned FROM users")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await m.answer("📭 База пользователей пуста.")
            return

        await m.answer(f"📊 **Всего зарегистрировано пользователей:** {len(rows)}", parse_mode="Markdown")

        for r in rows:
            t_id, uname, tier, is_banned = r[0], r[1] or "Без имени", r[2], r[3]
            status_icon = "🔴 Забанен" if is_banned == 1 else ("⭐ VIP" if tier == 'vip' else "👤 Free")
            
            builder = InlineKeyboardBuilder()
            if tier == 'free':
                builder.button(text="⭐ Выдать VIP", callback_data=f"setvip_{t_id}")
            else:
                builder.button(text="❌ Забрать VIP", callback_data=f"setfree_{t_id}")

            if is_banned == 1:
                builder.button(text="🟢 Разбанить", callback_data=f"unban_{t_id}")
            else:
                builder.button(text="🔴 Забанить", callback_data=f"ban_{t_id}")
            
            builder.adjust(2)

            card_text = f"👤 <b>{uname}</b>\nID: <code>{t_id}</code>\nСтатус: <b>{status_icon}</b>"
            await m.answer(card_text, reply_markup=builder.as_markup(), parse_mode="HTML")

    # Обработчики изменения тарифов и банов
    @dp.callback_query(F.data.startswith("setvip_") | F.data.startswith("setfree_") | F.data.startswith("ban_") | F.data.startswith("unban_"))
    async def process_user_action(callback: aiogram_types.CallbackQuery):
        if str(callback.from_user.id) != ADMIN_TELEGRAM_ID:
            return await callback.answer("Нет прав", show_alert=True)

        action, t_id = callback.data.split("_")
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        if action == "setvip":
            cursor.execute("UPDATE users SET tier = 'vip' WHERE telegram_id = ?", (t_id,))
            conn.commit()
            conn.close()
            await callback.answer("⭐ VIP выдан!")
            await callback.message.edit_text(callback.message.text + "\n\n✅ <i>Статус изменен на VIP</i>", parse_mode="HTML")
            try:
                await bot.send_message(chat_id=t_id, text="🎉 Поздравляем! Администратор выдал вам **VIP-статус**.")
            except Exception:
                pass

        elif action == "setfree":
            cursor.execute("UPDATE users SET tier = 'free' WHERE telegram_id = ?", (t_id,))
            conn.commit()
            conn.close()
            await callback.answer("👤 Статус изменен на Free")
            await callback.message.edit_text(callback.message.text + "\n\n✅ <i>Статус изменен на Free</i>", parse_mode="HTML")
            try:
                await bot.send_message(chat_id=t_id, text="ℹ️ Ваш статус был изменен администратором на **Free**.")
            except Exception:
                pass

        elif action == "ban":
            cursor.execute("UPDATE users SET is_banned = 1 WHERE telegram_id = ?", (t_id,))
            conn.commit()
            conn.close()
            await callback.answer("🔴 Пользователь забанен!")
            await callback.message.edit_text(callback.message.text + "\n\n❌ <i>ПОЛЬЗОВАТЕЛЬ ЗАБАНЕН</i>", parse_mode="HTML")
            try:
                await bot.send_message(
                    chat_id=t_id, 
                    text="❌ **Вы заблокированы администратором.**\n\nВы больше не можете получать коды для входа и использовать ИИ. Напишите в поддержку, если считаете это ошибкой.",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        elif action == "unban":
            cursor.execute("UPDATE users SET is_banned = 0 WHERE telegram_id = ?", (t_id,))
            conn.commit()
            conn.close()
            await callback.answer("🟢 Пользователь разбанен!")
            await callback.message.edit_text(callback.message.text + "\n\n✅ <i>ПОЛЬЗОВАТЕЛЬ РАЗБАНЕН</i>", parse_mode="HTML")
            try:
                await bot.send_message(chat_id=t_id, text="✅ **Ваш аккаунт разблокирован администратором!** Можете пользоваться ботом и сайтом.")
            except Exception:
                pass

    @dp.callback_query(F.data.startswith("delticket_"))
    async def process_delete_ticket(callback: aiogram_types.CallbackQuery):
        if str(callback.from_user.id) != ADMIN_TELEGRAM_ID:
            return
        ticket_id = callback.data.split("_")[1]
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM support_tickets WHERE id = ?", (ticket_id,))
        conn.commit()
        conn.close()

        await callback.message.delete()
        await callback.answer("Тикет удален.")

    @dp.callback_query(F.data.startswith("ans_"))
    async def process_answer_click(callback: aiogram_types.CallbackQuery):
        if str(callback.from_user.id) != ADMIN_TELEGRAM_ID:
            return
            
        target_id = callback.data.split("_")[1]
        admin_reply_targets[ADMIN_TELEGRAM_ID] = target_id
        
        await callback.message.answer(f"✍️ Напишите ответ для пользователя (ID: <code>{target_id}</code>) в следующем сообщении:", parse_mode="HTML")
        await callback.answer()

    @dp.message()
    async def global_message_handler(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        uname = m.from_user.username or m.from_user.first_name

        # Проверка бана для всех обычных текстовых сообщений
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT is_banned FROM users WHERE telegram_id = ?", (t_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0] == 1 and t_id != ADMIN_TELEGRAM_ID:
            await m.answer("❌ Ваш аккаунт заблокирован.")
            return

        # Обработка ответа админа на тикет
        if t_id == ADMIN_TELEGRAM_ID and t_id in admin_reply_targets:
            target_user_id = admin_reply_targets.pop(t_id)
            try:
                await bot.send_message(chat_id=target_user_id, text=f"💬 **Ответ от техподдержки:**\n\n{m.text}", parse_mode="Markdown")
                await m.answer("✅ Ответ успешно доставлен пользователю!")
            except Exception as e:
                await m.answer(f"❌ Ошибка отправки: {e}")
            return

        # Обработка поиска пользователя по введенному ID
        if t_id == ADMIN_TELEGRAM_ID and t_id in waiting_for_user_search:
            waiting_for_user_search.remove(t_id)
            search_query = m.text.strip()
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("SELECT telegram_id, username, tier, is_banned FROM users WHERE telegram_id = ?", (search_query,))
            row = cursor.fetchone()
            conn.close()

            if not row:
                await m.answer(f"❌ Пользователь с ID `{search_query}` не найден в базе данных.", parse_mode="Markdown")
                return

            found_t_id, found_uname, found_tier, found_banned = row[0], row[1] or "Без имени", row[2], row[3]
            status_icon = "🔴 Забанен" if found_banned == 1 else ("⭐ VIP" if found_tier == 'vip' else "👤 Free")
            
            builder = InlineKeyboardBuilder()
            if found_tier == 'free':
                builder.button(text="⭐ Выдать VIP", callback_data=f"setvip_{found_t_id}")
            else:
                builder.button(text="❌ Забрать VIP", callback_data=f"setfree_{found_t_id}")

            if found_banned == 1:
                builder.button(text="🟢 Разбанить", callback_data=f"unban_{found_t_id}")
            else:
                builder.button(text="🔴 Забанить", callback_data=f"ban_{found_t_id}")
            
            builder.adjust(2)

            card_text = f"🔎 **Результат поиска:**\n\n👤 <b>{found_uname}</b>\nID: <code>{found_t_id}</code>\nСтатус: <b>{status_icon}</b>"
            await m.answer(card_text, reply_markup=builder.as_markup(), parse_mode="HTML")
            return

        # Обработка отправки тикета поддержки пользователем
        if m.from_user.id in waiting_for_support:
            waiting_for_support.remove(m.from_user.id)
            support_text = m.text
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO support_tickets (telegram_id, username, message, created_at) VALUES (?, ?, ?, ?)", 
                           (t_id, uname, support_text, time.time()))
            conn.commit()
            conn.close()
            
            await m.answer("✅ Ваше обращение отправлено в поддержку! Администратор скоро ответит вам.")

            if ADMIN_TELEGRAM_ID:
                user_label = f"@{uname}" if m.from_user.username else uname
                notif_text = f"🆘 **Новый запрос в поддержку!**\n\n👤 От: {user_label} (ID: `{t_id}`)\n💬 Текст: {support_text}"
                
                builder = InlineKeyboardBuilder()
                builder.button(text="✍️ Ответить", callback_data=f"ans_{t_id}")
                
                try:
                    await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=notif_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
                except Exception:
                    pass
            return

    print("Bot started with ban/unban system successfully...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        print(f"Bot error: {e}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_telegram_bot())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
