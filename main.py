import os
import time
import uuid
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types
from google.genai.errors import APIError

app = FastAPI()

SERVER_BUILD_ID = str(uuid.uuid4())[:8]

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
            clean_prompt = "beautiful sunset landscape"
        
        import urllib.parse
        encoded_prompt = urllib.parse.quote(clean_prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true"
        
        return f"""Вот ваше сгенерированное изображение по запросу: *"{clean_prompt}"*

<div style="margin-top:10px;">
    <img src="{img_url}" alt="{clean_prompt}" style="max-width:100%; border-radius:12px; display:block; margin-bottom:8px;" />
    <a href="{img_url}" target="_blank" download="rubinov_ai.jpg" style="display:inline-block; background:#6366f1; color:#fff; padding:6px 12px; border-radius:8px; font-size:12px; text-decoration:none; font-weight:600;">📥 Скачать картинку в полном размере</a>
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
    return {"status": "ok", "build_id": SERVER_BUILD_ID}

@app.post("/api/chat")
async def chat_endpoint(
    prompt: str = Form(""),
    file: Optional[UploadFile] = File(None)
):
    if not prompt.strip() and not file:
        raise HTTPException(status_code=400, detail="Запрос или файл обязателен")
    
    file_bytes = None
    mime_type = None
    
    if file:
        file_bytes = await file.read()
        mime_type = file.content_type

    answer = get_gemini_response(prompt, file_bytes, mime_type)
    return {"response": answer}

@app.get("/", response_class=HTMLResponse)
async def get_chat_ui():
    return """
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
                --bg-main: #090a0f;
                --bg-sidebar: #0f1117;
                --card-bg: #161822;
                --border-color: rgba(255, 255, 255, 0.08);
                --border-hover: rgba(255, 255, 255, 0.18);
                --accent: #6366f1;
                --accent-hover: #4f46e5;
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --user-msg-bg: #1e2235;
                --bot-msg-bg: #11131c;
                --scrollbar-thumb: #050608;
            }

            * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
            html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); }
            body { display: flex; position: relative; }

            ::-webkit-scrollbar { width: 5px; height: 5px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb {
                background: var(--scrollbar-thumb);
                border-radius: 20px;
                border: 1px solid rgba(255, 255, 255, 0.05);
            }
            ::-webkit-scrollbar-thumb:hover { background: #000000; }

            #sidebar-overlay {
                display: none;
                position: fixed;
                top: 0; left: 0;
                width: 100vw; height: 100dvh;
                background: rgba(0, 0, 0, 0.6);
                backdrop-filter: blur(4px);
                z-index: 40;
                opacity: 0;
                transition: opacity 0.3s ease;
            }
            #sidebar-overlay.active { display: block; opacity: 1; }

            #sidebar { 
                width: 260px; 
                background: var(--bg-sidebar); 
                border-right: 1px solid var(--border-color); 
                display: flex; 
                flex-direction: column; 
                padding: 16px 12px; 
                z-index: 50;
                transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
                height: 100dvh;
            }
            .brand { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; padding: 0 4px; }
            
            .brand-logo-svg { 
                width: 30px; height: 30px; 
                filter: drop-shadow(0 0 8px rgba(239, 68, 68, 0.5));
                flex-shrink: 0;
            }
            
            .brand h2 { font-size: 14px; font-weight: 700; color: #ffffff; letter-spacing: -0.3px; }
            .brand span { font-size: 10px; color: var(--text-muted); font-weight: 500; display: block; }

            .btn-new-chat { 
                background: rgba(255, 255, 255, 0.04); color: #ffffff; 
                border: 1px solid var(--border-color); padding: 8px 12px; 
                border-radius: 10px; font-size: 12px; font-weight: 600; 
                cursor: pointer; display: flex; align-items: center; gap: 6px; 
                margin-bottom: 14px; transition: all 0.2s ease; 
            }
            .btn-new-chat:hover { background: rgba(255, 255, 255, 0.08); border-color: var(--border-hover); }

            .chats-header { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); font-weight: 700; margin-bottom: 8px; padding: 0 4px; text-transform: uppercase; letter-spacing: 0.8px; }
            
            #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; }
            .chat-item { 
                background: rgba(255, 255, 255, 0.02); 
                border: 1px solid var(--border-color); 
                border-radius: 8px; padding: 8px 10px; 
                font-size: 12px; color: #cbd5e1; 
                display: flex; justify-content: space-between; align-items: center; 
                cursor: pointer; transition: all 0.2s;
            }
            .chat-item.active { background: rgba(99, 102, 241, 0.15); border-color: var(--accent); color: #ffffff; }
            .chat-item:hover { background: rgba(255, 255, 255, 0.06); color: #ffffff; }
            .chat-item .close-btn { color: var(--text-muted); font-size: 14px; cursor: pointer; border-radius: 4px; width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; }
            .chat-item .close-btn:hover { color: #f87171; background: rgba(248, 113, 113, 0.1); }

            .sidebar-footer { font-size: 11px; color: var(--text-muted); display: flex; align-items: center; gap: 6px; margin-top: auto; padding-top: 12px; border-top: 1px solid var(--border-color); }
            .status-dot { width: 6px; height: 6px; background: #10b981; border-radius: 50%; box-shadow: 0 0 6px rgba(16, 185, 129, 0.5); }

            #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); position: relative; height: 100dvh; overflow: hidden; }
            
            #chat-header { 
                height: 50px; min-height: 50px;
                border-bottom: 1px solid var(--border-color); 
                display: flex; align-items: center; justify-content: space-between; 
                padding: 0 16px; background: rgba(15, 17, 23, 0.85); 
                backdrop-filter: blur(12px); z-index: 10;
            }
            .header-left { display: flex; align-items: center; gap: 10px; }
            .menu-toggle { display: none; background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-color); color: #ffffff; border-radius: 6px; padding: 6px; cursor: pointer; align-items: center; justify-content: center; }
            #chat-header h3 { font-size: 14px; font-weight: 600; color: #ffffff; }

            #chat-container { 
                flex: 1; overflow-y: auto; padding: 16px 16px 110px 16px; 
                display: flex; flex-direction: column; gap: 16px; 
                max-width: 900px; width: 100%; margin: 0 auto;
                position: relative;
            }

            .welcome-screen {
                position: absolute;
                top: 40%;
                left: 50%;
                transform: translate(-50%, -50%);
                text-align: center;
                user-select: none;
                pointer-events: none;
                width: 90%;
                max-width: 420px;
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 10px;
            }
            .welcome-avatar-svg {
                width: 64px; height: 64px;
                filter: drop-shadow(0 0 16px rgba(239, 68, 68, 0.6));
                margin-bottom: 4px;
            }
            .welcome-screen h1 { font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: -0.3px; }
            .welcome-screen p { font-size: 13px; color: var(--text-muted); line-height: 1.4; }
            
            .msg-row { display: flex; flex-direction: column; width: 100%; animation: fadeIn 0.25s ease-out; z-index: 2; }
            .msg-row.user-row { align-items: flex-end; }
            .msg-row.bot-row { align-items: flex-start; }

            @keyframes fadeIn { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }

            .msg-user { 
                background: var(--user-msg-bg); color: #ffffff; 
                border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px 12px 2px 12px; 
                padding: 10px 14px; font-size: 13px; line-height: 1.4; max-width: 85%; 
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15); word-break: break-word;
            }
            
            .msg-bot { 
                background: var(--bot-msg-bg); border: 1px solid var(--border-color); 
                color: #e2e8f0; border-radius: 12px 12px 12px 2px; 
                padding: 12px 16px; font-size: 13px; line-height: 1.5; max-width: 90%; 
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2); word-break: break-word;
            }

            .msg-bot p { margin-bottom: 10px; }
            .msg-bot p:last-child { margin-bottom: 0; }
            .msg-bot strong { color: #ffffff; font-weight: 700; }
            .msg-bot ul, .msg-bot ol { margin: 6px 0 10px 18px; }
            .msg-bot code { background: rgba(255, 255, 255, 0.08); padding: 2px 5px; border-radius: 4px; font-family: monospace; font-size: 12px; color: #f472b6; }
            .msg-bot pre { background: #090a0f; padding: 10px; border-radius: 6px; overflow-x: auto; margin: 8px 0; border: 1px solid var(--border-color); }
            
            .file-preview-tag { display: inline-flex; align-items: center; gap: 5px; background: rgba(255, 255, 255, 0.1); padding: 3px 6px; border-radius: 4px; font-size: 11px; margin-bottom: 4px; }

            /* Стили для отмененного сообщения */
            .cancelled-container {
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                width: 100%;
                animation: fadeIn 0.2s ease-out;
            }
            .cancelled-line {
                width: 100%;
                max-width: 85%;
                height: 1px;
                background: rgba(255, 255, 255, 0.1);
                margin: 6px 0 3px 0;
            }
            .cancelled-text {
                font-size: 10px;
                color: var(--text-muted);
                font-style: italic;
                letter-spacing: 0.3px;
                padding-right: 4px;
            }

            /* Лоадер с кнопкой отмены */
            .loader-box {
                display: flex;
                align-items: center;
                gap: 10px;
                background: var(--bot-msg-bg);
                border: 1px solid var(--border-color);
                border-radius: 12px 12px 12px 2px;
                padding: 10px 14px;
                font-size: 13px;
                color: var(--text-muted);
            }
            .btn-cancel-request {
                background: rgba(248, 113, 113, 0.1);
                border: 1px solid rgba(248, 113, 113, 0.3);
                color: #f87171;
                font-size: 11px;
                font-weight: 600;
                padding: 3px 8px;
                border-radius: 6px;
                cursor: pointer;
                transition: 0.2s;
            }
            .btn-cancel-request:hover {
                background: rgba(248, 113, 113, 0.2);
            }

            #input-wrapper {
                position: absolute; bottom: 0; left: 0; right: 0; 
                padding: 10px 14px; padding-bottom: calc(10px + env(safe-area-inset-bottom));
                background: linear-gradient(180deg, rgba(9, 10, 15, 0) 0%, rgba(9, 10, 15, 0.95) 40%, var(--bg-main) 100%);
                z-index: 20;
            }

            #input-container { 
                max-width: 900px; margin: 0 auto; background: var(--bg-sidebar); 
                border: 1px solid var(--border-color); border-radius: 14px; 
                padding: 6px 10px; display: flex; flex-direction: column; gap: 6px;
                box-shadow: 0 6px 20px rgba(0, 0, 0, 0.5);
                transition: border-color 0.2s;
            }
            #input-container.disabled {
                opacity: 0.6;
                pointer-events: none;
            }

            #file-info-bar { display: none; align-items: center; justify-content: space-between; background: rgba(255, 255, 255, 0.05); padding: 4px 10px; border-radius: 6px; font-size: 11px; color: var(--text-muted); }
            #file-info-bar span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 80%; }
            #file-info-bar button { background: none; border: none; color: #f87171; cursor: pointer; font-size: 13px; }

            .input-row { display: flex; gap: 6px; align-items: center; width: 100%; }

            .mini-btn { background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border-color); color: var(--text-muted); padding: 7px; border-radius: 8px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: 0.2s; }
            .mini-btn:hover { color: #ffffff; background: rgba(255, 255, 255, 0.1); border-color: var(--border-hover); }

            #prompt-input { flex: 1; background: transparent; border: none; color: #ffffff; font-size: 14px; outline: none; min-width: 0; }
            #prompt-input::placeholder { color: var(--text-muted); }
            
            .btn-send { background: var(--accent); color: #ffffff; border: none; border-radius: 8px; padding: 8px 14px; font-size: 12px; font-weight: 600; cursor: pointer; transition: all 0.2s ease; flex-shrink: 0; }
            .btn-send:hover { background: var(--accent-hover); }

            @media (max-width: 768px) {
                #sidebar { position: fixed; top: 0; left: 0; transform: translateX(-100%); }
                #sidebar.open { transform: translateX(0); }
                .menu-toggle { display: flex; }
                #chat-header { padding: 0 12px; }
                #chat-container { padding: 12px 12px 100px 12px; }
            }
        </style>
    </head>
    <body>
        <div id="sidebar-overlay" onclick="toggleSidebar(false)"></div>

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
                    <span>Official Assistant</span>
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
                <span class="status-dot"></span>
                <span>System Online</span>
            </div>
        </div>

        <div id="main">
            <div id="chat-header">
                <div class="header-left">
                    <button class="menu-toggle" onclick="toggleSidebar(true)">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
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
                        
                        <button class="btn-send" id="send-btn" onclick="sendMessage()">Отправить</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let chats = JSON.parse(localStorage.getItem('rubinov_chats_prod_v4') || '[]');
            let currentChatId = localStorage.getItem('rubinov_active_chat_prod_v4') || null;
            let selectedFile = null;
            let activeController = null; // Для отмены fetch-запроса

            if (chats.length === 0) {
                const initialChat = { id: Date.now().toString(), name: 'Новый чат 1', messages: [] };
                chats.push(initialChat);
                currentChatId = initialChat.id;
                saveState();
            } else if (!currentChatId || !chats.find(c => c.id === currentChatId)) {
                currentChatId = chats[0].id;
            }

            function saveState() {
                localStorage.setItem('rubinov_chats_prod_v4', JSON.stringify(chats));
                localStorage.setItem('rubinov_active_chat_prod_v4', currentChatId);
                renderChats();
            }

            function toggleSidebar(open) {
                const sidebar = document.getElementById('sidebar');
                const overlay = document.getElementById('sidebar-overlay');
                if (open) {
                    sidebar.classList.add('open');
                    overlay.classList.add('active');
                } else {
                    sidebar.classList.remove('open');
                    overlay.classList.remove('active');
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
                if (activeController) {
                    activeController.abort(); // Отменяем запрос при переключении чата
                }
                currentChatId = id;
                saveState();
                toggleSidebar(false);
            }

            function createNewChat() {
                if (chats.length >= 5) {
                    toggleSidebar(false);
                    return;
                }
                if (activeController) {
                    activeController.abort();
                }
                const newChat = {
                    id: Date.now().toString(),
                    name: `Новый чат ${chats.length + 1}`,
                    messages: []
                };
                chats.push(newChat);
                currentChatId = newChat.id;
                saveState();
                toggleSidebar(false);
            }

            function deleteChat(id) {
                chats = chats.filter(c => c.id !== id);
                if (currentChatId === id) {
                    currentChatId = chats[0].id;
                }
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
                        <svg class="welcome-avatar-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                            <path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="url(#rubyGrad)" stroke-width="4" fill="none" />
                            <path d="M15 35 L85 35 M50 10 L32 35 M50 10 L68 35 M50 90 L32 35 M50 90 L68 35" stroke="url(#rubyGrad)" stroke-width="2.5" opacity="0.7" />
                            <circle cx="50" cy="48" r="14" fill="#ff4b4b" opacity="0.25" />
                            <path d="M42 45 Q46 40 50 45 Q54 40 58 45 Q60 52 50 56 Q40 52 42 45 Z" stroke="#ffffff" stroke-width="2.5" fill="none" />
                        </svg>
                        <h1>Rubinov AI</h1>
                        <p>Задайте вопрос или попросите нарисовать картинку.</p>
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
                        if (msg.file) {
                            content += `<div class="file-preview-tag">📷 ${escapeHtml(msg.file)}</div><br>`;
                        }
                        content += escapeHtml(msg.text);
                        box.innerHTML = content;
                        row.appendChild(box);
                    } else if (msg.role === 'cancelled') {
                        const container = document.createElement('div');
                        container.className = 'cancelled-container';
                        
                        const box = document.createElement('div');
                        box.className = 'msg-user';
                        let content = '';
                        if (msg.file) {
                            content += `<div class="file-preview-tag">📷 ${escapeHtml(msg.file)}</div><br>`;
                        }
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
                        if (msg.text.includes('<img')) {
                            box.innerHTML = msg.text;
                        } else {
                            box.innerHTML = marked.parse(msg.text);
                        }
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
                if (e.key === 'Enter') sendMessage();
            }

            function setInputBlocked(blocked) {
                const inputContainer = document.getElementById('input-container');
                const promptInput = document.getElementById('prompt-input');
                const sendBtn = document.getElementById('send-btn');
                
                if (blocked) {
                    inputContainer.classList.add('disabled');
                    promptInput.disabled = true;
                    sendBtn.disabled = true;
                } else {
                    inputContainer.classList.remove('disabled');
                    promptInput.disabled = false;
                    sendBtn.disabled = false;
                    promptInput.focus();
                }
            }

            function cancelCurrentRequest() {
                if (activeController) {
                    activeController.abort();
                    activeController = null;
                }

                const activeChat = chats.find(c => c.id === currentChatId);
                if (activeChat && activeChat.messages.length > 0) {
                    const lastMsg = activeChat.messages[activeChat.messages.length - 1];
                    if (lastMsg.role === 'user') {
                        lastMsg.role = 'cancelled'; // Меняем роль на отмененное
                    }
                }

                document.getElementById('temp-loader-row')?.remove();
                setInputBlocked(false);
                saveState();
            }

            async function sendMessage() {
                const input = document.getElementById('prompt-input');
                const text = input.value.trim();
                
                if (!text && !selectedFile) return;

                const activeChat = chats.find(c => c.id === currentChatId);
                if (!activeChat) return;

                const userMsg = {
                    role: 'user',
                    text: text,
                    file: selectedFile ? selectedFile.name : null
                };

                activeChat.messages.push(userMsg);
                if (activeChat.messages.length === 1 && text) {
                    activeChat.name = text.slice(0, 18) + (text.length > 18 ? '...' : '');
                }
                
                renderMessages(activeChat.messages);

                const formData = new FormData();
                formData.append('prompt', text);
                if (selectedFile) {
                    formData.append('file', selectedFile);
                }

                input.value = '';
                removeSelectedFile();
                setInputBlocked(true);

                const chatContainer = document.getElementById('chat-container');
                const botRow = document.createElement('div');
                botRow.className = 'msg-row bot-row';
                botRow.id = 'temp-loader-row';
                botRow.innerHTML = `
                    <div class="loader-box">
                        <span>Думаю...</span>
                        <button class="btn-cancel-request" onclick="cancelCurrentRequest()">Отменить</button>
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
                    setInputBlocked(false);
                    activeController = null;

                    if (res.ok) {
                        activeChat.messages.push({ role: 'bot', text: data.response });
                    } else {
                        activeChat.messages.push({ role: 'bot', text: 'Ошибка: ' + (data.detail || 'Не удалось получить ответ.') });
                    }
                } catch (e) {
                    if (e.name === 'AbortError') {
                        // Запрос был отменен пользователем, ничего не делаем, отмена обработана в cancelCurrentRequest
                        return;
                    }
                    document.getElementById('temp-loader-row')?.remove();
                    setInputBlocked(false);
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

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
