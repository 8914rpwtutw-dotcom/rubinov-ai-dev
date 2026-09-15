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

def get_all_users_count_and_list():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id, username, is_banned, is_vip FROM users ORDER BY telegram_id DESC LIMIT 10")
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
        keyboard.append([KeyboardButton(text="👥 Пользователи"), KeyboardButton(text="🔍 Найти по ID")])
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
        f"Используйте кнопки меню ниже.",
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

@dp.message(F.text.contains("Тикеты"))
async def btn_tickets(message: types.Message):
    tg_id = str(message.from_user.id)
    username = message.from_user.username or "user"
    register_user_if_not_exists(tg_id, username)
    
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

    set_waiting_for_ticket(tg_id, 1)
    await message.answer(
        "💬 <b>Создание тикета в поддержку:</b>\n\n"
        "Напишите ваш вопрос или проблему следующим сообщением.",
        parse_mode="HTML"
    )

@dp.message(F.text.contains("Пользователи"))
async def btn_users_list(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    users = get_all_users_count_and_list()
    if not users:
        await message.answer("📭 Пользователей пока нет.")
        return
    
    await message.answer("👥 <b>Управление пользователями:</b>", parse_mode="HTML")
    for u_id, u_name, u_ban, u_vip in users:
        status_tags = []
        if u_ban: status_tags.append("⛔ БАН")
        if u_vip: status_tags.append("⭐ VIP")
        st_str = f" [{' | '.join(status_tags)}]" if status_tags else ""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⭐ VIP+" if not u_vip else "❌ Снять VIP", callback_data=f"toggle_vip_{u_id}"),
                InlineKeyboardButton(text="⛔ Бан" if not u_ban else "✅ Разбан", callback_data=f"toggle_ban_{u_id}")
            ]
        ])
        await message.answer(f"👤 <code>{u_id}</code> — @{u_name}{st_str}", parse_mode="HTML", reply_markup=kb)

@dp.callback_query(F.data.startswith("toggle_vip_"))
async def callback_toggle_vip(callback: types.CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_ID): return
    target_id = callback.data.split("_")[2]
    status = check_user_status(target_id)
    new_vip = 0 if status["is_vip"] else 1
    set_user_vip(target_id, new_vip)
    
    action_text = "выдан ⭐ VIP" if new_vip else "снят VIP"
    try: await bot.send_message(int(target_id), f"⭐ Вам {'выдан VIP статус!' if new_vip else 'снят VIP статус.'}")
    except: pass
    
    await callback.answer(f"Статус VIP изменен!")
    await callback.message.edit_text(f"👤 <code>{target_id}</code> — статус обновлен: {action_text}", parse_mode="HTML")

@dp.callback_query(F.data.startswith("toggle_ban_"))
async def callback_toggle_ban(callback: types.CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_ID): return
    target_id = callback.data.split("_")[2]
    status = check_user_status(target_id)
    new_ban = 0 if status["is_banned"] else 1
    set_user_ban(target_id, new_ban)
    
    action_text = "забанен ⛔" if new_ban else "разбанен ✅"
    try: await bot.send_message(int(target_id), f"❌ Вы забанены администратором." if new_ban else "✅ Вы разбанены!")
    except: pass
    
    await callback.answer(f"Статус блокировки изменен!")
    await callback.message.edit_text(f"👤 <code>{target_id}</code> — статус обновлен: {action_text}", parse_mode="HTML")

@dp.message(F.text.contains("Найти по ID"))
async def btn_search_user_prompt(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    await message.answer(
        "🔍 <b>Поиск пользователя:</b>\n"
        "Отправьте команду в формате:\n<code>/user [TELEGRAM_ID]</code>",
        parse_mode="HTML"
    )

@dp.message(F.text.contains("Админ-панель"))
async def btn_admin_panel(message: types.Message):
    tg_id = str(message.from_user.id)
    if tg_id != str(ADMIN_ID): return
    await message.answer(
        "👑 <b>Панель администратора:</b>\n"
        "• /user [ID] — инфо о пользователе\n"
        "• /reply [ID] [текст] — ответить на тикет",
        parse_mode="HTML"
    )

@dp.message(Command("user"))
async def cmd_user_info(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("❌ Формат: /user [ID]")
        return
    target_id = args[1]
    status = check_user_status(target_id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="⭐ Сменить VIP", callback_data=f"toggle_vip_{target_id}"),
            InlineKeyboardButton(text="⛔ Сменить Бан", callback_data=f"toggle_ban_{target_id}")
        ]
    ])
    await message.answer(
        f"👤 <b>Информация о пользователе <code>{target_id}</code>:</b>\n"
        f"• Забанен: {'Да' if status['is_banned'] else 'Нет'}\n"
        f"• VIP статус: {'Активен' if status['is_vip'] else 'Нет'}",
        parse_mode="HTML", reply_markup=kb
    )

@dp.message(Command("reply"))
async def cmd_reply(message: types.Message):
    if str(message.from_user.id) != str(ADMIN_ID): return
    parts = message.text.split(" ", 2)
    if len(parts) < 3:
        await message.answer("❌ Формат: /reply ID текст")
        return
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
        return f'Вот изображение: *"{clean_prompt}"*<div style="margin-top:14px;"><img src="{img_url}" style="max-width:100%; border-radius:16px; display:block; margin-bottom:12px;" /><a href="{img_url}" target="_blank" download="image.jpg" style="background:linear-gradient(135deg, #00f2fe, #4facfe); color:#000; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:700;">📥 Скачать</a></div>'

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

# ==================== СОВРЕМЕННЫЙ ДИЗАЙН ИНТЕРФЕЙСА ====================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Rubinov AI — Neural Interface</title>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        :root {
            --bg-base: #07080b;
            --bg-sidebar: #0d0f17;
            --bg-chat: #090a0f;
            --border-color: rgba(0, 242, 254, 0.12);
            --neon-cyan: #00f2fe;
            --neon-blue: #4facfe;
            --text-main: #f1f5f9;
            --text-muted: #64748b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Space Grotesk', sans-serif; }
        body { background: var(--bg-base); color: var(--text-main); height: 100dvh; display: flex; overflow: hidden; }

        #auth-overlay {
            position: fixed; inset: 0; background: rgba(7, 8, 11, 0.92); backdrop-filter: blur(16px);
            z-index: 9999; display: flex; align-items: center; justify-content: center;
        }
        .auth-box {
            background: #0d0f17; border: 1px solid var(--neon-cyan); padding: 36px; border-radius: 24px;
            width: 380px; text-align: center; box-shadow: 0 0 40px rgba(0, 242, 254, 0.15);
        }
        .auth-box h2 { margin-bottom: 8px; font-size: 22px; color: #fff; letter-spacing: 0.5px; }
        .auth-box p { font-size: 13px; color: var(--text-muted); margin-bottom: 24px; line-height: 1.4; }
        .auth-box a {
            display: inline-block; background: rgba(0, 242, 254, 0.08); color: var(--neon-cyan); border: 1px solid rgba(0, 242, 254, 0.2);
            padding: 10px 18px; border-radius: 12px; text-decoration: none; font-weight: 600; font-size: 13px; margin-bottom: 24px;
            transition: 0.2s;
        }
        .auth-box a:hover { background: rgba(0, 242, 254, 0.15); box-shadow: 0 0 15px rgba(0, 242, 254, 0.3); }
        .auth-box input {
            width: 100%; background: #050608; border: 1px solid var(--border-color); color: var(--neon-cyan);
            padding: 14px; border-radius: 14px; font-size: 24px; text-align: center; letter-spacing: 8px; outline: none; margin-bottom: 16px;
        }
        .auth-box input:focus { border-color: var(--neon-cyan); box-shadow: 0 0 10px rgba(0, 242, 254, 0.2); }
        .auth-box button {
            width: 100%; background: linear-gradient(135deg, var(--neon-blue), var(--neon-cyan)); border: none;
            color: #000; padding: 14px; border-radius: 14px; font-weight: 700; cursor: pointer; font-size: 14px;
            transition: 0.2s;
        }
        .auth-box button:hover { opacity: 0.9; box-shadow: 0 0 20px rgba(0, 242, 254, 0.4); }

        aside {
            width: 280px; background: var(--bg-sidebar); border-right: 1px solid var(--border-color);
            display: flex; flex-direction: column; padding: 20px; gap: 16px;
        }
        .brand-area { display: flex; align-items: center; justify-content: space-between; }
        .brand-area h1 { font-size: 16px; font-weight: 700; color: #fff; background: linear-gradient(135deg, #fff, var(--neon-cyan)); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .vip-tag { background: linear-gradient(135deg, #f59e0b, #d97706); color: #000; font-size: 10px; font-weight: 800; padding: 4px 8px; border-radius: 6px; box-shadow: 0 0 10px rgba(245, 158, 11, 0.4); }
        
        .btn-new {
            background: linear-gradient(135deg, var(--neon-blue), var(--neon-cyan)); border: none; color: #000;
            padding: 12px; border-radius: 12px; font-weight: 700; font-size: 13px; cursor: pointer; text-align: center;
            transition: 0.2s;
        }
        .btn-new:hover { box-shadow: 0 0 15px rgba(0, 242, 254, 0.3); }
        .chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
        .chat-row {
            background: rgba(255,255,255,0.01); border: 1px solid transparent; padding: 10px 12px; border-radius: 10px;
            font-size: 13px; color: var(--text-muted); cursor: pointer; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; transition: 0.2s;
        }
        .chat-row:hover { color: #fff; background: rgba(255,255,255,0.03); }
        .chat-row.active { background: rgba(0, 242, 254, 0.08); border-color: rgba(0, 242, 254, 0.25); color: var(--neon-cyan); }

        main { flex: 1; display: flex; flex-direction: column; background: var(--bg-chat); position: relative; }
        header {
            height: 64px; border-bottom: 1px solid var(--border-color); display: flex; align-items: center;
            justify-content: space-between; padding: 0 28px; background: rgba(9, 10, 15, 0.8); backdrop-filter: blur(10px);
        }
        header h3 { font-size: 14px; font-weight: 600; color: #fff; }

        .chat-messages {
            flex: 1; overflow-y: auto; padding: 28px; display: flex; flex-direction: column; gap: 20px; max-width: 900px; width: 100%; margin: 0 auto;
        }
        .message { padding: 16px 20px; border-radius: 18px; font-size: 14px; line-height: 1.6; max-width: 85%; }
        .message.user { background: rgba(0, 242, 254, 0.1); border: 1px solid rgba(0, 242, 254, 0.25); align-self: flex-end; border-bottom-right-radius: 4px; color: #fff; }
        .message.bot { background: #0d0f17; border: 1px solid var(--border-color); align-self: flex-start; border-bottom-left-radius: 4px; }

        .input-area { padding: 20px 28px; background: var(--bg-base); border-top: 1px solid var(--border-color); }
        .input-box {
            max-width: 900px; margin: 0 auto; background: #0d0f17; border: 1px solid var(--border-color);
            border-radius: 16px; display: flex; align-items: center; padding: 8px 14px; gap: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.5);
        }
        .input-box input {
            flex: 1; background: transparent; border: none; color: #fff; font-size: 14px; outline: none; padding: 8px;
        }
        .input-box button {
            background: linear-gradient(135deg, var(--neon-blue), var(--neon-cyan)); border: none; color: #000;
            padding: 10px 20px; border-radius: 12px; font-weight: 700; cursor: pointer; font-size: 13px; transition: 0.2s;
        }
        .input-box button:hover { box-shadow: 0 0 15px rgba(0, 242, 254, 0.4); }
    </style>
</head>
<body>

    <div id="auth-overlay" style="display:none;">
        <div class="auth-box">
            <h2>Авторизация</h2>
            <p>Запросите код в Telegram-боте и введите его ниже:</p>
            <a href="https://t.me/Rubinov_Ai_bot" target="_blank">🤖 Открыть @Rubinov_Ai_bot</a>
            <input type="text" id="code-input" placeholder="000000" maxlength="6">
            <button onclick="auth()">Подключиться</button>
        </div>
    </div>

    <aside>
        <div class="brand-area">
            <h1>Rubinov AI</h1>
            <div id="vip-slot"></div>
        </div>
        <button class="btn-new" onclick="newChat()">+ Новый чат</button>
        <div class="chats-list" id="chats-list"></div>
        <button onclick="logout()" style="background:none; border:none; color:var(--text-muted); cursor:pointer; font-size:12px; text-align:left; padding:4px;">Выйти из аккаунта</button>
    </aside>

    <main>
        <header>
            <h3 id="header-title">Нейросеть</h3>
            <div id="header-status" style="font-size:12px; color:var(--text-muted);"></div>
        </header>

        <div class="chat-messages" id="chat-messages"></div>

        <div class="input-area">
            <div class="input-box">
                <input type="text" id="user-input" placeholder="Введите ваш запрос..." onkeydown="if(event.key==='Enter') send()">
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
                document.getElementById('header-status').textContent = 'Neural VIP Active';
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
