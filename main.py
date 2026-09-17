import os
import json
import time
import sqlite3
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from google import genai
from google.genai import types
from google.genai.errors import APIError
import httpx

app = FastAPI()

# Поддержка HTTPS заголовков прокси Render
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["*"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# --- Настройка базы данных пользователей (SQLite) ---
DB_FILE = "rubinov_users.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            status TEXT DEFAULT 'free', -- 'free', 'vip', 'banned'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Твой административный email
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "8914rpwtutw@gmail.com")

def get_user_status(email: str) -> str:
    if not email:
        return "guest"
    if email.lower() == ADMIN_EMAIL.lower():
        return "admin"
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM users WHERE email = ?", (email.lower(),))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return row[0] # 'free', 'vip', 'banned'
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (email, status) VALUES (?, 'free')", (email.lower(),))
    conn.commit()
    conn.close()
    return "free"

MODELS = ["gemini-2.5-flash", "gemini-3.1-flash-lite", "gemini-2.5-pro"]

current_key_idx = 0
current_model_idx = 0

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

def get_redirect_uri(request: Request) -> str:
    env_uri = os.getenv("REDIRECT_URI") or os.getenv("GOOGLE_REDIRECT_URI")
    if env_uri:
        return env_uri
    return str(request.url_for("auth_google_callback"))

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
    <a href="{img_url}" target="_blank" download="rubinov_ai.jpg" style="display:inline-flex; align-items: center; gap: 8px; background: linear-gradient(135deg, #6366f1, #a855f7); color:#fff; padding:9px 18px; border-radius:12px; font-size:12px; text-decoration:none; font-weight:600; box-shadow: 0 4px 20px rgba(99, 102, 241, 0.35); transition: all 0.2s ease;">📥 Скачать в высоком разрешении</a>
</div>"""

    api_keys = get_api_keys()
    if not api_keys:
        raise HTTPException(
            status_code=500,
            detail="API-ключи не найдены в Environment Variables."
        )

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
            response = client.models.generate_content(
                model=active_model,
                contents=contents
            )
            return response.text

        except APIError as e:
            if e.code in [503, 429] or "RESOURCE_EXHAUSTED" in str(e) or "UNAVAILABLE" in str(e) or "high demand" in str(e).lower():
                current_model_idx += 1
                if current_model_idx >= num_models:
                    current_model_idx = 0
                    current_key_idx = (current_key_idx + 1) % num_keys
                time.sleep(0.5)
                continue
            elif e.code == 404 or "not found" in str(e).lower():
                current_model_idx = (current_model_idx + 1) % num_models
                continue
            else:
                break
        except Exception:
            break

    raise HTTPException(
        status_code=500,
        detail="Сервис ИИ перегружен в данный момент. Повторите попытку через несколько секунд."
    )

@app.get("/health")
def health_check():
    return {"status": "ok"}

# --- Маршруты Google OAuth ---
@app.get("/auth/google")
def login_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="GOOGLE_CLIENT_ID не настроен")
    
    current_redirect_uri = get_redirect_uri(request)
    
    google_auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={GOOGLE_CLIENT_ID}&"
        f"redirect_uri={current_redirect_uri}&"
        f"response_type=code&"
        f"scope=openid%20email%20profile"
    )
    return RedirectResponse(google_auth_url)

@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: Optional[str] = None, error: Optional[str] = None):
    if error:
        raise HTTPException(status_code=400, detail=f"Google вернул ошибку: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Код авторизации (code) не получен от Google.")

    token_url = "https://oauth2.googleapis.com/token"
    current_redirect_uri = get_redirect_uri(request)
    
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": current_redirect_uri,
        "grant_type": "authorization_code",
    }
    
    async with httpx.AsyncClient() as client:
        token_res = await client.post(token_url, data=payload)
        if token_res.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Ошибка обмена токена с Google: {token_res.text}")
        
        token_data = token_res.json()
        access_token = token_data.get("access_token")
        
        user_res = await client.get(
            "https://www.googleapis.com/oauth2/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if user_res.status_code != 200:
            raise HTTPException(status_code=400, detail="Не удалось получить профиль пользователя")
        
        user_info = user_res.json()
        user_email = user_info.get("email")

    get_user_status(user_email)

    host_url = str(request.base_url)
    response = RedirectResponse(url=host_url, status_code=303)
    response.set_cookie(
        key="user_email",
        value=user_email,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=86400 * 30
    )
    return response

@app.get("/auth/logout")
def logout(request: Request):
    host_url = str(request.base_url)
    response = RedirectResponse(url=host_url, status_code=303)
    response.delete_cookie(key="user_email")
    return response

# --- API Админ-панели ---
@app.get("/api/admin/users")
def admin_get_users(request: Request):
    user_email = request.cookies.get("user_email")
    if get_user_status(user_email) != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT email, status, created_at FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    
    users_list = [{"email": r[0], "status": r[1], "created_at": r[2]} for r in rows]
    return {"users": users_list}

@app.post("/api/admin/update-status")
async def admin_update_status(request: Request):
    user_email = request.cookies.get("user_email")
    if get_user_status(user_email) != "admin":
        raise HTTPException(status_code=403, detail="Доступ запрещен")
    
    body = await request.json()
    target_email = body.get("email")
    new_status = body.get("status")
    
    if new_status not in ["free", "vip", "banned"]:
        raise HTTPException(status_code=400, detail="Неверный статус")
    
    if target_email.lower() == ADMIN_EMAIL.lower():
        raise HTTPException(status_code=400, detail="Нельзя изменить статус главного администратора")

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET status = ? WHERE email = ?", (new_status, target_email.lower()))
    conn.commit()
    conn.close()
    
    return {"success": True}

@app.post("/api/chat")
async def chat_endpoint(
    request: Request,
    prompt: str = Form(""),
    file: Optional[UploadFile] = File(None)
):
    user_email = request.cookies.get("user_email")
    status = get_user_status(user_email)

    if status == "banned":
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован администратором.")

    guest_count = int(request.cookies.get("guest_requests", "0"))

    if not user_email:
        if guest_count >= 10:
            raise HTTPException(
                status_code=403, 
                detail="Исчерпан лимит (10 запросов) для гостя. Войдите через Google аккаунт, чтобы снять ограничения."
            )

    if not prompt.strip() and not file:
        raise HTTPException(status_code=400, detail="Запрос или файл обязателен")
    
    file_bytes = None
    mime_type = None
    
    if file:
        file_bytes = await file.read()
        mime_type = file.content_type

    answer = get_gemini_response(prompt, file_bytes, mime_type)
    
    response_data = {"response": answer}
    json_resp = JSONResponse(content=response_data)

    if not user_email:
        json_resp.set_cookie(key="guest_requests", value=str(guest_count + 1), httponly=False)

    return json_resp

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

        body::before {
            content: '';
            position: fixed;
            top: -15vh; left: -15vw;
            width: 55vw; height: 55vh;
            background: radial-gradient(circle, rgba(99, 102, 241, 0.07) 0%, rgba(168, 85, 247, 0.02) 60%, transparent 80%);
            z-index: 0; pointer-events: none; filter: blur(80px);
        }
        body::after {
            content: '';
            position: fixed;
            bottom: -15vh; right: -15vw;
            width: 55vw; height: 55vh;
            background: radial-gradient(circle, rgba(168, 85, 247, 0.06) 0%, rgba(236, 72, 153, 0.01) 60%, transparent 80%);
            z-index: 0; pointer-events: none; filter: blur(80px);
        }

        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 20px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(168, 85, 247, 0.3); }

        #sidebar-overlay {
            display: none; position: fixed; top: 0; left: 0;
            width: 100vw; height: 100dvh;
            background: rgba(4, 5, 8, 0.7); backdrop-filter: blur(6px);
            z-index: 40; opacity: 0; transition: opacity 0.3s ease;
        }
        #sidebar-overlay.active { display: block; opacity: 1; }

        #sidebar { 
            width: 280px; min-width: 280px;
            background: var(--bg-sidebar); backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border-right: 1px solid var(--border-color); 
            display: flex; flex-direction: column; padding: 20px 14px; 
            z-index: 50; height: 100dvh;
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), margin-left 0.3s cubic-bezier(0.16, 1, 0.3, 1);
            box-shadow: 15px 0 40px rgba(0, 0, 0, 0.4);
        }
        
        body.sidebar-collapsed #sidebar { margin-left: -280px; }

        .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; padding: 0 4px; }
        .brand-logo-svg { width: 32px; height: 32px; filter: drop-shadow(0 0 12px rgba(168, 85, 247, 0.5)); flex-shrink: 0; }
        .brand h2 { font-size: 15px; font-weight: 700; color: #ffffff; letter-spacing: -0.3px; }
        .brand span { font-size: 10px; color: var(--text-muted); font-weight: 500; display: block; }

        .btn-new-chat { 
            background: var(--accent-gradient); color: #ffffff; border: none; padding: 11px 16px; 
            border-radius: 14px; font-size: 12px; font-weight: 600; cursor: pointer; 
            display: flex; align-items: center; gap: 9px; margin-bottom: 18px; 
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1); box-shadow: 0 4px 20px rgba(99, 102, 241, 0.3);
        }
        .btn-new-chat:hover { transform: translateY(-1px); box-shadow: 0 6px 25px rgba(168, 85, 247, 0.45); }

        .chats-header { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); font-weight: 700; margin-bottom: 8px; padding: 0 4px; text-transform: uppercase; letter-spacing: 0.8px; }
        
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding-right: 2px; }
        .chat-item { 
            background: rgba(255, 255, 255, 0.015); border: 1px solid var(--border-color); 
            border-radius: 12px; padding: 10px 12px; font-size: 12px; color: #cbd5e1; 
            display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: all 0.2s ease;
        }
        .chat-item.active { 
            background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.35); 
            color: #ffffff; box-shadow: 0 0 20px rgba(168, 85, 247, 0.08); 
        }
        .chat-item:hover { background: rgba(255, 255, 255, 0.04); border-color: var(--border-hover); color: #ffffff; }
        .chat-item .close-btn { color: var(--text-muted); font-size: 14px; cursor: pointer; border-radius: 6px; width: 22px; height: 22px; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
        .chat-item .close-btn:hover { color: #f87171; background: rgba(248, 113, 113, 0.15); }

        .sidebar-footer { font-size: 11px; color: var(--text-muted); display: flex; flex-direction: column; gap: 8px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border-color); }
        .footer-info { display: flex; align-items: center; justify-content: space-between; width: 100%; }
        .footer-left-status { display: flex; align-items: center; gap: 8px; }
        .status-dot { width: 7px; height: 7px; background: #a855f7; border-radius: 50%; box-shadow: 0 0 10px rgba(168, 85, 247, 0.8); }

        .btn-google-login {
            background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);
            color: #ffffff; padding: 8px 12px; border-radius: 10px; font-size: 11.5px; font-weight: 600;
            text-decoration: none; display: flex; align-items: center; justify-content: center; gap: 8px; transition: all 0.2s ease;
        }
        .btn-google-login:hover { background: rgba(255, 255, 255, 0.1); border-color: rgba(168, 85, 247, 0.4); }

        .btn-admin-panel {
            background: rgba(168, 85, 247, 0.15); border: 1px solid rgba(168, 85, 247, 0.4);
            color: #d8b4fe; padding: 8px 12px; border-radius: 10px; font-size: 11.5px; font-weight: 600;
            cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 6px; transition: all 0.2s ease;
            width: 100%; margin-bottom: 4px;
        }
        .btn-admin-panel:hover { background: rgba(168, 85, 247, 0.25); color: #fff; }

        #admin-modal {
            display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100dvh;
            background: rgba(4, 5, 8, 0.8); backdrop-filter: blur(8px); z-index: 100;
            align-items: center; justify-content: center; padding: 20px;
        }
        #admin-modal.active { display: flex; }
        .admin-box {
            background: #0d1018; border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 20px; width: 100%; max-width: 650px; max-height: 80dvh;
            display: flex; flex-direction: column; overflow: hidden;
            box-shadow: 0 20px 50px rgba(0,0,0,0.8), 0 0 30px rgba(168,85,247,0.15);
        }
        .admin-header {
            padding: 16px 20px; border-bottom: 1px solid var(--border-color);
            display: flex; justify-content: space-between; align-items: center;
            background: rgba(18, 21, 31, 0.6);
        }
        .admin-header h3 { font-size: 15px; color: #fff; font-weight: 700; }
        .admin-close { background: none; border: none; color: var(--text-muted); font-size: 18px; cursor: pointer; }
        .admin-close:hover { color: #f87171; }
        .admin-content { padding: 16px 20px; overflow-y: auto; flex: 1; }
        .admin-table { width: 100%; border-collapse: collapse; font-size: 12px; }
        .admin-table th, .admin-table td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border-color); }
        .admin-table th { color: var(--text-muted); font-weight: 600; }
        .badge { padding: 3px 8px; border-radius: 6px; font-size: 10.5px; font-weight: 600; text-transform: uppercase; }
        .badge-free { background: rgba(148, 163, 184, 0.15); color: #94a3b8; }
        .badge-vip { background: rgba(168, 85, 247, 0.2); color: #d8b4fe; border: 1px solid rgba(168,85,247,0.3); }
        .badge-banned { background: rgba(248, 113, 113, 0.2); color: #f87171; border: 1px solid rgba(248,113,113,0.3); }
        .admin-actions-cell select {
            background: rgba(255,255,255,0.05); border: 1px solid var(--border-color);
            color: #fff; padding: 4px 8px; border-radius: 8px; font-size: 11px; outline: none; cursor: pointer;
        }

        #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); position: relative; height: 100dvh; overflow: hidden; z-index: 1; }
        
        #chat-header { 
            height: 60px; min-height: 60px; border-bottom: 1px solid var(--border-color); 
            display: flex; align-items: center; justify-content: space-between; 
            padding: 0 24px; background: rgba(4, 5, 8, 0.5); backdrop-filter: blur(16px); z-index: 10;
        }
        .header-left { display: flex; align-items: center; gap: 14px; }
        
        .menu-toggle { 
            background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); 
            color: #ffffff; border-radius: 12px; padding: 8px; cursor: pointer; 
            display: flex; align-items: center; justify-content: center; transition: all 0.2s ease;
        }
        .menu-toggle:hover { background: rgba(168, 85, 247, 0.1); border-color: rgba(168, 85, 247, 0.3); }
        #chat-header h3 { font-size: 14px; font-weight: 600; color: #ffffff; letter-spacing: -0.2px; }

        #chat-container { 
            flex: 1; overflow-y: auto; padding: 24px 24px 140px 24px; 
            display: flex; flex-direction: column; gap: 22px; 
            max-width: 900px; width: 100%; margin: 0 auto; position: relative; z-index: 2;
        }

        .welcome-screen {
            position: absolute; top: 45%; left: 50%; transform: translate(-50%, -50%);
            text-align: center; user-select: none; pointer-events: none; width: 90%; max-width: 480px;
            display: flex; flex-direction: column; align-items: center; gap: 16px;
            animation: fadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1);
        }
        .welcome-avatar-glow {
            position: relative; padding: 20px; border-radius: 28px;
            background: rgba(168, 85, 247, 0.04); border: 1px solid rgba(168, 85, 247, 0.15);
            box-shadow: 0 0 50px rgba(168, 85, 247, 0.12); margin-bottom: 4px;
        }
        .welcome-avatar-svg { width: 60px; height: 60px; filter: drop-shadow(0 0 18px rgba(168, 85, 247, 0.6)); }
        .welcome-screen h1 { font-size: 24px; font-weight: 700; color: #ffffff; letter-spacing: -0.5px; }
        .welcome-screen p { font-size: 13.5px; color: var(--text-muted); line-height: 1.6; }
        
        .msg-row { display: flex; flex-direction: column; width: 100%; animation: messageIn 0.35s cubic-bezier(0.16, 1, 0.3, 1); z-index: 2; }
        .msg-row.user-row { align-items: flex-end; }
        .msg-row.bot-row { align-items: flex-start; }

        @keyframes messageIn { from { opacity: 0; transform: translateY(12px) scale(0.99); } to { opacity: 1; transform: translateY(0) scale(1); } }
        @keyframes fadeIn { from { opacity: 0; transform: translate(-50%, -46%); } to { opacity: 1; transform: translate(-50%, -50%); } }

        .msg-user { 
            background: var(--user-msg-bg); color: #ffffff; 
            border: 1px solid rgba(168, 85, 247, 0.22); border-radius: 18px 18px 4px 18px; 
            padding: 13px 18px; font-size: 13.5px; line-height: 1.55; max-width: 85%; 
            box-shadow: 0 10px 30px rgba(99, 102, 241, 0.12); word-break: break-word; backdrop-filter: blur(12px);
        }
        
        .msg-bot { 
            background: var(--bot-msg-bg); border: 1px solid var(--border-color); 
            color: #e2e8f0; border-radius: 18px 18px 18px 4px; 
            padding: 18px 22px; font-size: 13.5px; line-height: 1.65; max-width: 90%; 
            box-shadow: 0 12px 35px rgba(0, 0, 0, 0.35); word-break: break-word; backdrop-filter: blur(20px);
        }

        .msg-bot p { margin-bottom: 12px; }
        .msg-bot p:last-child { margin-bottom: 0; }
        .msg-bot strong { color: #ffffff; font-weight: 700; }
        .msg-bot ul, .msg-bot ol { margin: 8px 0 12px 20px; }
        .msg-bot code { background: rgba(255, 255, 255, 0.06); padding: 3px 7px; border-radius: 6px; font-family: monospace; font-size: 12px; color: #f472b6; border: 1px solid rgba(255,255,255,0.04); }
        .msg-bot pre { background: #020305; padding: 14px; border-radius: 12px; overflow-x: auto; margin: 12px 0; border: 1px solid var(--border-color); }
        
        .file-preview-tag { display: inline-flex; align-items: center; gap: 6px; background: rgba(168, 85, 247, 0.15); padding: 5px 10px; border-radius: 8px; font-size: 11px; margin-bottom: 8px; border: 1px solid rgba(168, 85, 247, 0.3); color: #d8b4fe; }

        .cancelled-container { display: flex; flex-direction: column; align-items: flex-end; width: 100%; animation: messageIn 0.2s ease-out; }
        .cancelled-line { width: 100%; max-width: 85%; height: 1px; background: rgba(255, 255, 255, 0.06); margin: 8px 0 4px 0; }
        .cancelled-text { font-size: 10px; color: var(--text-muted); font-style: italic; letter-spacing: 0.3px; padding-right: 4px; }

        .loader-box {
            display: flex; align-items: center; gap: 12px; background: var(--bot-msg-bg);
            border: 1px solid var(--border-color); border-radius: 18px 18px 18px 4px;
            padding: 14px 20px; font-size: 13.5px; color: var(--text-muted); backdrop-filter: blur(20px);
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
        }
        .spinner {
            width: 16px; height: 16px; border: 2px solid rgba(168,85,247,0.2);
            border-top-color: #a855f7; border-radius: 50%; animation: spin 0.8s linear infinite;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        #input-wrapper {
            position: absolute; bottom: 0; left: 0; right: 0; 
            padding: 16px 24px; padding-bottom: calc(16px + env(safe-area-inset-bottom));
            background: linear-gradient(180deg, rgba(4, 5, 8, 0) 0%, rgba(4, 5, 8, 0.85) 40%, var(--bg-main) 100%);
            z-index: 20;
        }

        #input-container { 
            max-width: 900px; margin: 0 auto; background: rgba(13, 16, 24, 0.8); 
            backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
            border: 1px solid rgba(168, 85, 247, 0.18); border-radius: 20px; 
            padding: 8px 12px; display: flex; flex-direction: column; gap: 8px;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6), 0 0 25px rgba(168, 85, 247, 0.06);
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        #input-container:focus-within {
            border-color: rgba(168, 85, 247, 0.45);
            box-shadow: 0 14px 45px rgba(0, 0, 0, 0.7), 0 0 30px rgba(168, 85, 247, 0.15);
        }

        #file-info-bar { display: none; align-items: center; justify-content: space-between; background: rgba(168, 85, 247, 0.12); padding: 6px 12px; border-radius: 10px; font-size: 11.5px; color: #d8b4fe; border: 1px solid rgba(168, 85, 247, 0.25); }
        #file-info-bar span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 80%; }
        #file-info-bar button { background: none; border: none; color: #f87171; cursor: pointer; font-size: 14px; transition: transform 0.2s; }
        #file-info-bar button:hover { transform: scale(1.15); }

        .input-row { display: flex; gap: 8px; align-items: center; width: 100%; }

        .mini-btn { 
            background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); 
            color: var(--text-muted); padding: 10px; border-radius: 12px; cursor: pointer; 
            display: flex; align-items: center; justify-content: center; transition: all 0.2s ease; 
        }
        .mini-btn:hover { color: #ffffff; background: rgba(168, 85, 247, 0.12); border-color: rgba(168, 85, 247, 0.3); transform: translateY(-1px); }

        #prompt-input { flex: 1; background: transparent; border: none; color: #ffffff; font-size: 14px; outline: none; min-width: 0; padding: 6px 4px; }
        #prompt-input::placeholder { color: var(--text-muted); }
        #prompt-input:disabled { opacity: 0.5; }
        
        .btn-action { 
            background: var(--accent-gradient); color: #ffffff; border: none; border-radius: 12px; 
            padding: 11px 20px; font-size: 12.5px; font-weight: 600; cursor: pointer; 
            transition: all 0.25s cubic-bezier(0.16, 1, 0.3, 1); flex-shrink: 0; display: flex; align-items: center; justify-content: center; min-width: 100px;
            box-shadow: 0 4px 20px rgba(99, 102, 241, 0.3);
        }
        .btn-action:hover { transform: translateY(-1px); box-shadow: 0 6px 25px rgba(168, 85, 247, 0.45); }
        
        .btn-action.cancel-mode {
            background: var(--cancel-bg); border: 1px solid var(--cancel-border);
            color: var(--cancel-color); box-shadow: 0 4px 20px rgba(248, 113, 113, 0.15);
        }
        .btn-action.cancel-mode:hover { background: rgba(248, 113, 113, 0.22); }

        @media (max-width: 768px) {
            #sidebar { position: fixed; top: 0; left: 0; margin-left: 0 !important; transform: translateX(-100%); }
            #sidebar.mobile-open { transform: translateX(0); }
            #chat-header { padding: 0 16px; }
            #chat-container { padding: 16px 16px 120px 16px; }
            #input-wrapper { padding: 12px 16px; }
        }
    </style>
</head>
<body>
    <div id="sidebar-overlay" onclick="toggleSidebar()"></div>

    <div id="admin-modal" onclick="closeAdminModal(event)">
        <div class="admin-box" onclick="event.stopPropagation()">
            <div class="admin-header">
                <h3>👑 Панель администратора</h3>
                <button class="admin-close" onclick="toggleAdminModal(false)">×</button>
            </div>
            <div class="admin-content">
                <table class="admin-table">
                    <thead>
                        <tr>
                            <th>Email</th>
                            <th>Статус</th>
                            <th>Действие</th>
                        </tr>
                    </thead>
                    <tbody id="admin-users-list">
                        <tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Загрузка...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div id="sidebar">
        <div class="brand">
            <svg class="brand-logo-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                <defs>
                    <linearGradient id="rubyGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" stop-color="#ff4b4b" />
                        <stop offset="100%" stop-color="#900c3f" />
                    </linearGradient>
                </defs>
                <path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="url(#rubyGrad)" stroke-width="4" fill="none" />
                <path d="M15 35 L85 35 M50 10 L32 35 M50 10 L68 35 M50 90 L32 35 M50 90 L68 35" stroke="url(#rubyGrad)" stroke-width="2.5" opacity="0.7" />
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
            USER_AUTH_BLOCK
            <div class="footer-info">
                <div class="footer-left-status">
                    <span class="status-dot"></span>
                    <span>Online</span>
                </div>
            </div>
        </div>
    </div>

    <div id="main">
        <div id="chat-header">
            <div class="header-left">
                <button class="menu-toggle" onclick="toggleSidebar()" title="Меню">
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
                    <button onclick="removeSelectedFile()">✕</button>
                </div>
                <div class="input-row">
                    <input type="file" id="file-input" accept="image/*" capture="environment" style="display: none;" onchange="handleFileSelect(event)" />
                    
                    <button class="mini-btn" onclick="document.getElementById('file-input').click()" title="Прикрепить фото">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                    </button>

                    <button class="mini-btn" onclick="triggerImageGenerationPrompt()" title="Сгенерировать картинку">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                    </button>

                    <input type="text" id="prompt-input" placeholder="Введите сообщение или опишите картинку..." onkeydown="handleKeyPress(event)" />
                    
                    <button class="btn-action" id="action-btn" onclick="handleActionButton()">Отправить</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        let chats = JSON.parse(localStorage.getItem('rubinov_chats_main_v1') || '[]');
        let currentChatId = localStorage.getItem('rubinov_active_chat_main_v1') || null;
        let selectedFile = null;
        let activeController = null;
        let isGenerating = false;

        if (chats.length === 0) {
            const initialChat = { id: Date.now().toString(), name: 'Новый чат 1', messages: [] };
            chats.push(initialChat);
            currentChatId = initialChat.id;
            saveState();
        } else if (!currentChatId || !chats.find(c => c.id === currentChatId)) {
            currentChatId = chats[0].id;
        }

        function saveState() {
            localStorage.setItem('rubinov_chats_main_v1', JSON.stringify(chats));
            localStorage.setItem('rubinov_active_chat_main_v1', currentChatId);
            renderChats();
        }

        function toggleSidebar() {
            const width = window.innerWidth;
            if (width <= 768) {
                const sidebar = document.getElementById('sidebar');
                const overlay = document.getElementById('sidebar-overlay');
                sidebar.classList.toggle('mobile-open');
                overlay.classList.toggle('active');
            } else {
                document.body.classList.toggle('sidebar-collapsed');
            }
        }

        function toggleAdminModal(show) {
            const modal = document.getElementById('admin-modal');
            if (show) {
                modal.classList.add('active');
                loadAdminUsers();
            } else {
                modal.classList.remove('active');
            }
        }

        function closeAdminModal(e) {
            if (e.target.id === 'admin-modal') {
                toggleAdminModal(false);
            }
        }

        async function loadAdminUsers() {
            const tbody = document.getElementById('admin-users-list');
            tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:var(--text-muted);">Загрузка...</td></tr>';
            try {
                const res = await fetch('/api/admin/users');
                if (!res.ok) throw new Error('Ошибка доступа');
                const data = await res.json();
                
                tbody.innerHTML = '';
                data.users.forEach(u => {
                    const tr = document.createElement('tr');
                    tr.innerHTML = `
                        <td style="word-break:break-all;">${escapeHtml(u.email)}</td>
                        <td><span class="badge badge-${u.status}">${u.status}</span></td>
                        <td class="admin-actions-cell">
                            <select onchange="updateUserStatus('${u.email}', this.value)">
                                <option value="free" ${u.status === 'free' ? 'selected' : ''}>Free</option>
                                <option value="vip" ${u.status === 'vip' ? 'selected' : ''}>VIP</option>
                                <option value="banned" ${u.status === 'banned' ? 'selected' : ''}>Ban</option>
                            </select>
                        </td>
                    `;
                    tbody.appendChild(tr);
                });
            } catch (err) {
                tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; color:#f87171;">Не удалось загрузить список пользователей.</td></tr>';
            }
        }

        async function updateUserStatus(email, status) {
            try {
                const res = await fetch('/api/admin/update-status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, status })
                });
                if (!res.ok) {
                    const errData = await res.json();
                    alert(errData.detail || 'Ошибка при изменении статуса');
                }
                loadAdminUsers();
            } catch (e) {
                alert('Ошибка соединения с сервером');
            }
        }

        function renderChats() {
            const list = document.getElementById('chats-list');
            list.innerHTML = '';
            document.getElementById('chat-count').textContent = chats.length;

            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `chat-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.onclick = () => switchChat(chat.id);

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

        function switchChat(id) {
            if (activeController) activeController.abort();
            currentChatId = id;
            setGeneratingState(false);
            saveState();
            if (window.innerWidth <= 768) toggleSidebar();
        }

        function createNewChat() {
            if (chats.length >= 5) {
                if (window.innerWidth <= 768) toggleSidebar();
                return;
            }
            if (activeController) activeController.abort();
            const newChat = {
                id: Date.now().toString(),
                name: `Новый чат ${chats.length + 1}`,
                messages: []
            };
            chats.push(newChat);
            currentChatId = newChat.id;
            setGeneratingState(false);
            saveState();
            if (window.innerWidth <= 768) toggleSidebar();
        }

        function deleteChat(id) {
            chats = chats.filter(c => c.id !== id);
            if (currentChatId === id) currentChatId = chats[0].id;
            saveState();
        }

        function triggerImageGenerationPrompt() {
            const input = document.getElementById('prompt-input');
            input.value = "Нарисуй: ";
            input.focus();
        }

        function renderMessages(messages) {
            const chatContainer = document.getElementById('chat-container');
            chatContainer.innerHTML = '';

            if (messages.length === 0) {
                const welcome = document.createElement('div');
                welcome.className = 'welcome-screen';
                welcome.innerHTML = `
                    <div class="welcome-avatar-glow">
                        <svg class="welcome-avatar-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="url(#rubyGrad)" stroke-width="4" fill="none" />
                            <path d="M15 35 L85 35 M50 10 L32 35 M50 10 L68 35 M50 90 L32 35 M50 90 L68 35" stroke="url(#rubyGrad)" stroke-width="2.5" opacity="0.7" />
                            <circle cx="50" cy="48" r="14" fill="#ff4b4b" opacity="0.25" />
                            <path d="M42 45 Q46 40 50 45 Q54 40 58 45 Q60 52 50 56 Q40 52 42 45 Z" stroke="#ffffff" stroke-width="2.5" fill="none" />
                        </svg>
                    </div>
                    <h1>Rubinov AI</h1>
                    <p>Чем я могу помочь вам сегодня?</p>
                `;
                chatContainer.appendChild(welcome);
                return;
            }

            messages.forEach(msg => {
                const row = document.createElement('div');
                row.className = `msg-row ${msg.role === 'user' ? 'user-row' : 'bot-row'}`;
                
                if (msg.role === 'user') {
                    const box = document.createElement('div');
                    box.className = 'msg-user';
                    let content = '';
                    if (msg.file) content += `<div class="file-preview-tag">📷 ${escapeHtml(msg.file)}</div><br>`;
                    content += escapeHtml(msg.text);
                    box.innerHTML = content;
                    row.appendChild(box);
                } else if (msg.role === 'cancelled') {
                    const container = document.createElement('div');
                    container.className = 'cancelled-container';
                    const box = document.createElement('div');
                    box.className = 'msg-user';
                    let content = '';
                    if (msg.file) content += `<div class="file-preview-tag">📷 ${escapeHtml(msg.file)}</div><br>`;
                    content += escapeHtml(msg.text);
                    box.innerHTML = content;
                    container.appendChild(box);
                    
                    const line = document.createElement('div');
                    line.className = 'cancelled-line';
                    container.appendChild(line);

                    const smallText = document.createElement('div');
                    smallText.className = 'cancelled-text';
                    smallText.textContent = 'сообщение отменено';
                    container.appendChild(smallText);
                    row.appendChild(container);
                } else {
                    const box = document.createElement('div');
                    box.className = 'msg-bot';
                    if (msg.text.includes('<img')) box.innerHTML = msg.text;
                    else box.innerHTML = marked.parse(msg.text);
                    row.appendChild(box);
                }

                chatContainer.appendChild(row);
            });
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }

        function handleFileSelect(event) {
            const file = event.target.files[0];
            if (file) {
                selectedFile = file;
                document.getElementById('file-name-text').textContent = `📷 ${file.name}`;
                document.getElementById('file-info-bar').style.display = 'flex';
            }
        }

        function removeSelectedFile() {
            selectedFile = null;
            document.getElementById('file-input').value = '';
            document.getElementById('file-info-bar').style.display = 'none';
        }

        function handleKeyPress(e) {
            if (e.key === 'Enter') handleActionButton();
        }

        function setGeneratingState(generating) {
            isGenerating = generating;
            const actionBtn = document.getElementById('action-btn');
            const promptInput = document.getElementById('prompt-input');

            if (generating) {
                actionBtn.textContent = 'Отменить';
                actionBtn.className = 'btn-action cancel-mode';
                promptInput.disabled = true;
            } else {
                actionBtn.textContent = 'Отправить';
                actionBtn.className = 'btn-action';
                promptInput.disabled = false;
                promptInput.focus();
            }
        }

        function handleActionButton() {
            if (isGenerating) cancelCurrentRequest();
            else sendMessage();
        }

        function cancelCurrentRequest() {
            if (activeController) {
                activeController.abort();
                activeController = null;
            }
            const activeChat = chats.find(c => c.id === currentChatId);
            if (activeChat && activeChat.messages.length > 0) {
                const lastMsg = activeChat.messages[activeChat.messages.length - 1];
                if (lastMsg.role === 'user') lastMsg.role = 'cancelled';
            }
            document.getElementById('temp-loader-row')?.remove();
            setGeneratingState(false);
            saveState();
        }

        async function sendMessage() {
            const input = document.getElementById('prompt-input');
            const text = input.value.trim();
            if (!text && !selectedFile) return;

            const activeChat = chats.find(c => c.id === currentChatId);
            if (!activeChat) return;

            const userMsg = { role: 'user', text: text, file: selectedFile ? selectedFile.name : null };
            activeChat.messages.push(userMsg);
            if (activeChat.messages.length === 1 && text) {
                activeChat.name = text.slice(0, 18) + (text.length > 18 ? '...' : '');
            }
            
            renderMessages(activeChat.messages);

            const formData = new FormData();
            formData.append('prompt', text);
            if (selectedFile) formData.append('file', selectedFile);

            input.value = '';
            removeSelectedFile();
            setGeneratingState(true);

            const chatContainer = document.getElementById('chat-container');
            const botRow = document.createElement('div');
            botRow.className = 'msg-row bot-row';
            botRow.id = 'temp-loader-row';
            botRow.innerHTML = `
                <div class="loader-box">
                    <div class="spinner"></div>
                    <span>Думаю...</span>
                </div>
            `;
            chatContainer.appendChild(botRow);
            chatContainer.scrollTop = chatContainer.scrollHeight;

            activeController = new AbortController();

            try {
                const res = await fetch('/api/chat', {
                    method: 'POST',
                    body: formData,
                    signal: activeController.signal
                });
                const data = await res.json();

                document.getElementById('temp-loader-row')?.remove();
                setGeneratingState(false);
                activeController = null;

                if (res.ok) {
                    activeChat.messages.push({ role: 'bot', text: data.response });
                } else {
                    activeChat.messages.push({ role: 'bot', text: 'Ошибка: ' + (data.detail || 'Не удалось получить ответ.') });
                }
            } catch (e) {
                if (e.name === 'AbortError') return;
                document.getElementById('temp-loader-row')?.remove();
                setGeneratingState(false);
                activeController = null;
                activeChat.messages.push({ role: 'bot', text: 'Ошибка подключения к серверу.' });
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
async def get_chat_ui(request: Request):
    user_email = request.cookies.get("user_email")
    status = get_user_status(user_email)
    guest_count = int(request.cookies.get("guest_requests", "0"))
    
    if user_email:
        admin_btn_html = f"""<button class="btn-admin-panel" onclick="toggleAdminModal(true)">👑 Админ-панель</button>""" if status == "admin" else ""
        auth_block = f"""
            {admin_btn_html}
            <div style="font-size: 11px; color: #a855f7; word-break: break-all; margin-bottom: 4px;">👤 {user_email}</div>
            <a href="/auth/logout" style="color: #f87171; font-size: 11px; text-decoration: none; margin-bottom: 6px; display: inline-block;">Выйти из аккаунта</a>
        """
    else:
        left_limit = max(0, 10 - guest_count)
        auth_block = f"""
            <div style="font-size: 11px; color: #94a3b8; margin-bottom: 6px;">Бесплатно запросов: <b>{left_limit}/10</b></div>
            <a href="/auth/google" class="btn-google-login">
                <svg width="14" height="14" viewBox="0 0 24 24"><path fill="#EA4335" d="M12 5c1.6 0 3 .6 4.1 1.6l3.1-3.1C17.3 1.8 14.8 1 12 1 7.4 1 3.5 3.6 1.6 7.4l3.7 2.9C6.2 7.3 8.9 5 12 5z"/><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5c-.3 1.5-1.1 2.8-2.4 3.7l3.7 2.9c2.2-2 3.7-5 3.7-8.8z"/><path fill="#FBBC05" d="M5.3 14.7c-.2-.7-.4-1.5-.4-2.7s.2-2 .4-2.7L1.6 6.4C.6 8.4 0 10.6 0 13s.6 4.6 1.6 6.6l3.7-2.9z"/><path fill="#34A853" d="M12 23c3.2 0 6-1.1 8-3l-3.7-2.9c-1.1.7-2.5 1.2-4.3 1.2-3.1 0-5.8-2.3-6.7-5.3L1.6 15.6C3.5 19.4 7.4 23 12 23z"/></svg>
                Войти через Google
            </a>
        """

    rendered_html = HTML_TEMPLATE.replace("USER_AUTH_BLOCK", auth_block)
    return rendered_html

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
