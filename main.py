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
    from google.genai.errors import APIError
    from aiogram import Bot, Dispatcher, F, types as aiogram_types
    from aiogram.filters import Command
    from aiogram.utils.keyboard import InlineKeyboardBuilder
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
    # Пользователи
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            telegram_id TEXT PRIMARY KEY,
            username TEXT,
            tier TEXT DEFAULT 'free'
        )
    ''')
    # Коды входа
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS login_codes (
            code TEXT PRIMARY KEY,
            telegram_id TEXT,
            expires_at REAL
        )
    ''')
    # Активные сессии (лимит устройств)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_sessions (
            telegram_id TEXT,
            session_id TEXT PRIMARY KEY
        )
    ''')
    # Таблица обращений в техподдержку (тикеты)
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
        return payload.get("telegram_id")
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
    if time.time() > expires_at:
        cursor.execute("DELETE FROM login_codes WHERE code = ?", (code,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=400, detail="Срок действия кода истек.")
    
    cursor.execute("SELECT tier FROM users WHERE telegram_id = ?", (telegram_id,))
    user_row = cursor.fetchone()
    tier = user_row[0] if user_row else 'free'
    
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
            <p style="font-size: 13px; color: #94a3b8; margin-top: 8px;">Запросите код в нашем Telegram-боте командой /login</p>
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
@app.on_event("startup")
async def on_startup():
    if not TELEGRAM_BOT_TOKEN:
        return
        
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    dp = Dispatcher()

    # Словарь памяти для администратора: какому пользователю он сейчас пишет ответ
    admin_reply_targets = {}

    @dp.message(Command("start"))
    async def cmd_start(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        uname = m.from_user.username or m.from_user.first_name
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username, tier) VALUES (?, ?, 'free')", (t_id, uname))
        conn.commit()
        conn.close()

        if t_id == ADMIN_TELEGRAM_ID:
            await m.answer(
                "👑 **Панель Администратора**\n\n"
                "📌 **Доступные команды:**\n"
                "👥 /users — список всех пользователей (выдача/снятие VIP)\n"
                "📬 /tickets — посмотреть активные запросы в поддержку"
            )
        else:
            await m.answer(
                "👋 Добро пожаловать в **Rubinov AI**!\n\n"
                "📌 **Команды:**\n"
                "👉 /login — получить код для входа на сайт\n"
                "🆘 /support [текст] — написать в техническую поддержку"
            )

    @dp.message(Command("login"))
    async def cmd_login(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        uname = m.from_user.username or m.from_user.first_name
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (telegram_id, username, tier) VALUES (?, ?, 'free')", (t_id, uname))
        conn.commit()
        
        code = str(random.randint(100000, 999999))
        cursor.execute("INSERT OR REPLACE INTO login_codes (code, telegram_id, expires_at) VALUES (?, ?, ?)", (code, t_id, time.time() + 60))
        conn.commit()
        conn.close()
        
        await m.answer(f"🔐 Ваш код для входа на сайт:\n\n`{code}`\n\n⚠️ *Действителен 1 минуту.*", parse_mode="Markdown")

    @dp.message(Command("support"))
    async def cmd_support(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        uname = m.from_user.username or m.from_user.first_name
        text_parts = m.text.split(maxsplit=1)
        
        if len(text_parts) < 2:
            await m.answer("⚠️ Напишите ваш вопрос вместе с командой. Пример:\n`/support Помогите разобраться со входом`", parse_mode="Markdown")
            return
            
        support_text = text_parts[1]
        
        # Сохраняем тикет в базу данных
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO support_tickets (telegram_id, username, message, created_at) VALUES (?, ?, ?, ?)", 
                       (t_id, uname, support_text, time.time()))
        conn.commit()
        conn.close()
        
        await m.answer("✅ Ваше обращение отправлено в поддержку! Администратор скоро ответит вам.")

        # Уведомляем администратора
        if ADMIN_TELEGRAM_ID:
            user_label = f"@{uname}" if m.from_user.username else uname
            notif_text = f"🆘 **Новый запрос в поддержку!**\n\n👤 От: {user_label} (ID: `{t_id}`)\n💬 Текст: {support_text}"
            
            builder = InlineKeyboardBuilder()
            builder.button(text="✍️ Ответить", callback_data=f"ans_{t_id}")
            
            try:
                await bot.send_message(chat_id=ADMIN_TELEGRAM_ID, text=notif_text, reply_markup=builder.as_markup(), parse_mode="Markdown")
            except Exception:
                pass

    # --- АДМИН: СПИСОК ТИКЕТОВ ПОДДЕРЖКИ ---
    @dp.message(Command("tickets"))
    async def cmd_tickets(m: aiogram_types.Message):
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

    # --- АДМИН: СПИСОК ПОЛЬЗОВАТЕЛЕЙ И УПРАВЛЕНИЕ VIP ---
    @dp.message(Command("users"))
    async def cmd_admin_users(m: aiogram_types.Message):
        if str(m.from_user.id) != ADMIN_TELEGRAM_ID:
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id, username, tier FROM users")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            await m.answer("📭 База пользователей пуста.")
            return

        await m.answer(f"📊 **Всего зарегистрировано пользователей:** {len(rows)}", parse_mode="Markdown")

        for r in rows:
            t_id, uname, tier = r[0], r[1] or "Без имени", r[2]
            status_icon = "⭐ VIP" if tier == 'vip' else "👤 Free"
            
            builder = InlineKeyboardBuilder()
            if tier == 'free':
                builder.button(text="⭐ Выдать VIP", callback_data=f"setvip_{t_id}")
            else:
                builder.button(text="❌ Забрать VIP", callback_data=f"setfree_{t_id}")

            card_text = f"👤 <b>{uname}</b>\nID: <code>{t_id}</code>\nСтатус: <b>{status_icon}</b>"
            await m.answer(card_text, reply_markup=builder.as_markup(), parse_mode="HTML")

    # --- КНОПКИ УПРАВЛЕНИЯ И ОТВЕТОВ ---
    @dp.callback_query(F.data.startswith("setvip_") | F.data.startswith("setfree_"))
    async def process_tier_change(callback: aiogram_types.CallbackQuery):
        if str(callback.from_user.id) != ADMIN_TELEGRAM_ID:
            return await callback.answer("Нет прав", show_alert=True)

        action, t_id = callback.data.split("_")
        new_tier = 'vip' if action == 'setvip' else 'free'

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET tier = ? WHERE telegram_id = ?", (new_tier, t_id))
        conn.commit()
        conn.close()

        status_msg = "⭐ VIP статус выдан!" if new_tier == 'vip' else "👤 Статус изменен на Free."
        await callback.message.edit_text(callback.message.text + f"\n\n✅ <i>{status_msg}</i>", parse_mode="HTML")
        await callback.answer(status_msg)

        try:
            if new_tier == 'vip':
                await bot.send_message(chat_id=t_id, text="🎉 Поздравляем! Администратор выдал вам **VIP-статус** (лимит устройств увеличен до 3).")
            else:
                await bot.send_message(chat_id=t_id, text="ℹ️ Ваш статус был изменен администратором на **Free**.")
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

    # Перехват текста от админа для отправки клиенту
    @dp.message()
    async def admin_chat_handler(m: aiogram_types.Message):
        t_id = str(m.from_user.id)
        if t_id == ADMIN_TELEGRAM_ID and t_id in admin_reply_targets:
            target_user_id = admin_reply_targets.pop(t_id)
            try:
                await bot.send_message(chat_id=target_user_id, text=f"💬 **Ответ от техподдержки:**\n\n{m.text}", parse_mode="Markdown")
                await m.answer("✅ Ответ успешно доставлен пользователю!")
            except Exception as e:
                await m.answer(f"❌ Ошибка отправки: {e}")

    print("Bot started with tickets and admin controls...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    except Exception as e:
        print(f"Bot error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
