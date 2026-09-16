import os
import time
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types
from google.genai.errors import APIError

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
            --bg-main: #06070b;
            --bg-sidebar: rgba(12, 14, 22, 0.85);
            --card-bg: rgba(18, 21, 31, 0.5);
            --border-color: rgba(255, 255, 255, 0.08);
            --border-hover: rgba(168, 85, 247, 0.3);
            --accent-gradient: linear-gradient(135deg, #7c3aed 0%, #a855f7 100%);
            --accent-glow: rgba(168, 85, 247, 0.2);
            --cancel-bg: rgba(248, 113, 113, 0.12);
            --cancel-border: rgba(248, 113, 113, 0.35);
            --cancel-color: #f87171;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --user-msg-bg: linear-gradient(135deg, rgba(124, 58, 237, 0.2) 0%, rgba(168, 85, 247, 0.15) 100%);
            --bot-msg-bg: rgba(15, 18, 28, 0.85);
            --scrollbar-thumb: rgba(255, 255, 255, 0.1);
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', -apple-system, sans-serif; -webkit-tap-highlight-color: transparent; }
        html, body { height: 100%; height: 100dvh; overflow: hidden; background: var(--bg-main); color: var(--text-main); }
        body { display: flex; position: relative; }

        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 20px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(168, 85, 247, 0.3); }

        #sidebar-overlay {
            display: none; position: fixed; top: 0; left: 0;
            width: 100vw; height: 100dvh; background: rgba(4, 5, 8, 0.7);
            backdrop-filter: blur(6px); z-index: 40; opacity: 0; transition: opacity 0.3s ease;
        }
        #sidebar-overlay.active { display: block; opacity: 1; }

        #sidebar { 
            width: 260px; min-width: 260px;
            background: var(--bg-sidebar); backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
            border-right: 1px solid var(--border-color); display: flex; flex-direction: column; 
            padding: 20px 14px; z-index: 50; height: 100dvh;
            transition: transform 0.3s cubic-bezier(0.16, 1, 0.3, 1), margin-left 0.3s cubic-bezier(0.16, 1, 0.3, 1);
        }
        
        body.sidebar-collapsed #sidebar { margin-left: -260px; }

        .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; padding: 0 4px; }
        .brand-logo-svg { width: 30px; height: 30px; filter: drop-shadow(0 0 10px rgba(168, 85, 247, 0.6)); flex-shrink: 0; }
        .brand h2 { font-size: 16px; font-weight: 700; color: #ffffff; letter-spacing: -0.3px; }
        .brand span { font-size: 10px; color: var(--text-muted); font-weight: 500; display: block; }

        .btn-new-chat { 
            background: var(--accent-gradient); color: #ffffff; border: none; padding: 12px 16px; 
            border-radius: 14px; font-size: 13px; font-weight: 600; cursor: pointer; 
            display: flex; align-items: center; justify-content: center; gap: 8px; 
            margin-bottom: 20px; transition: all 0.2s ease; box-shadow: 0 4px 20px rgba(124, 58, 237, 0.35);
        }
        .btn-new-chat:hover { opacity: 0.95; transform: translateY(-1px); }

        .chats-header { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); font-weight: 700; margin-bottom: 10px; padding: 0 4px; text-transform: uppercase; letter-spacing: 0.8px; }
        #chats-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 6px; padding-right: 2px; }
        .chat-item { 
            background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border-color); 
            border-radius: 12px; padding: 10px 12px; font-size: 12.5px; color: #cbd5e1; 
            display: flex; justify-content: space-between; align-items: center; cursor: pointer; transition: all 0.2s ease;
        }
        .chat-item.active { background: rgba(168, 85, 247, 0.12); border-color: rgba(168, 85, 247, 0.4); color: #ffffff; }
        .chat-item:hover { background: rgba(255, 255, 255, 0.05); color: #ffffff; }
        .chat-item .close-btn { color: var(--text-muted); font-size: 14px; cursor: pointer; border-radius: 6px; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; }
        .chat-item .close-btn:hover { color: #f87171; background: rgba(248, 113, 113, 0.15); }

        .sidebar-footer { font-size: 11px; color: var(--text-muted); display: flex; align-items: center; gap: 8px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--border-color); }
        .status-dot { width: 7px; height: 7px; background: #22c55e; border-radius: 50%; box-shadow: 0 0 10px rgba(34, 197, 94, 0.8); }

        #main { flex: 1; display: flex; flex-direction: column; background: var(--bg-main); position: relative; height: 100dvh; overflow: hidden; }
        
        #chat-header { 
            height: 56px; min-height: 56px; border-bottom: 1px solid var(--border-color); 
            display: flex; align-items: center; justify-content: space-between; padding: 0 20px; 
            background: rgba(6, 7, 11, 0.7); backdrop-filter: blur(16px); z-index: 10;
        }
        .header-left { display: flex; align-items: center; gap: 12px; }
        .menu-toggle { background: transparent; border: none; color: #ffffff; cursor: pointer; display: flex; align-items: center; }
        #chat-header h3 { font-size: 14px; font-weight: 600; color: #ffffff; }

        #chat-container { 
            flex: 1; overflow-y: auto; padding: 24px 20px 120px 20px; 
            display: flex; flex-direction: column; gap: 20px; 
            width: 100%; max-width: 900px; margin: 0 auto; position: relative; z-index: 2;
        }

        .welcome-screen {
            position: absolute; top: 40%; left: 50%; transform: translate(-50%, -50%);
            text-align: center; width: 100%; max-width: 480px; display: flex; flex-direction: column; align-items: center; gap: 14px;
        }
        .welcome-avatar-svg { width: 54px; height: 54px; filter: drop-shadow(0 0 16px rgba(168, 85, 247, 0.6)); }
        .welcome-screen h1 { font-size: 22px; font-weight: 700; color: #ffffff; }
        .welcome-screen p { font-size: 13px; color: var(--text-muted); }

        .msg-row { display: flex; flex-direction: column; width: 100%; animation: messageIn 0.25s ease-out; }
        .msg-row.user-row { align-items: flex-end; }
        .msg-row.bot-row { align-items: flex-start; }
        @keyframes messageIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

        .msg-user { 
            background: var(--user-msg-bg); color: #ffffff; border: 1px solid rgba(168, 85, 247, 0.25); 
            border-radius: 16px 16px 4px 16px; padding: 12px 16px; font-size: 13.5px; line-height: 1.5; max-width: 85%; 
        }
        .msg-bot { 
            background: var(--bot-msg-bg); border: 1px solid var(--border-color); color: #e2e8f0; 
            border-radius: 16px 16px 16px 4px; padding: 16px 20px; font-size: 13.5px; line-height: 1.6; max-width: 90%; 
        }

        #input-wrapper {
            position: absolute; bottom: 0; left: 0; right: 0; 
            padding: 16px 20px; background: linear-gradient(180deg, transparent 0%, var(--bg-main) 60%); z-index: 20;
        }

        #input-container { 
            max-width: 860px; margin: 0 auto; background: rgba(15, 18, 28, 0.9); 
            backdrop-filter: blur(20px); border: 1px solid rgba(168, 85, 247, 0.25); 
            border-radius: 16px; padding: 8px 12px; display: flex; flex-direction: column; gap: 6px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
        }

        #file-info-bar { display: none; align-items: center; justify-content: space-between; background: rgba(168, 85, 247, 0.15); padding: 6px 12px; border-radius: 8px; font-size: 11px; color: #d8b4fe; }
        #file-info-bar button { background: none; border: none; color: #f87171; cursor: pointer; }

        .input-row { display: flex; gap: 8px; align-items: center; width: 100%; }
        .mini-btn { background: transparent; border: none; color: var(--text-muted); padding: 8px; cursor: pointer; border-radius: 8px; display: flex; align-items: center; justify-content: center; }
        .mini-btn:hover { color: #ffffff; background: rgba(255, 255, 255, 0.05); }

        #prompt-input { flex: 1; background: transparent; border: none; color: #ffffff; font-size: 13.5px; outline: none; padding: 6px 4px; }
        #prompt-input::placeholder { color: var(--text-muted); }
        
        .btn-action { 
            background: var(--accent-gradient); color: #ffffff; border: none; border-radius: 10px; 
            padding: 9px 16px; font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all 0.2s ease;
        }
        .btn-action.cancel-mode { background: var(--cancel-bg); border: 1px solid var(--cancel-border); color: var(--cancel-color); }

        @media (max-width: 768px) {
            #sidebar { position: fixed; top: 0; left: 0; margin-left: 0 !important; transform: translateX(-100%); }
            #sidebar.mobile-open { transform: translateX(0); }
        }
    </style>
</head>
<body>
    <div id="sidebar-overlay" onclick="toggleSidebar()"></div>

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
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 5v14M5 12h14"/></svg>
            Новый диалог
        </button>

        <div class="chats-header">
            <span>Чаты (<span id="chat-count">1</span>/5)</span>
        </div>

        <div id="chats-list"></div>

        <div class="sidebar-footer">
            <span class="status-dot"></span>
            <span>Online</span>
        </div>
    </div>

    <div id="main">
        <div id="chat-header">
            <div class="header-left">
                <button class="menu-toggle" onclick="toggleSidebar()" title="Меню">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h18M3 6h18M3 18h18"/></svg>
                </button>
                <h3 id="current-chat-title">Новый чат 1</h3>
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
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
                    </button>

                    <button class="mini-btn" onclick="triggerImageGenerationPrompt()" title="Сгенерировать картинку">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
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

        function renderChats() {
            const list = document.getElementById('chats-list');
            list.innerHTML = '';
            document.getElementById('chat-count').textContent = chats.length;

            chats.forEach(chat => {
                const item = document.createElement('div');
                item.className = `chat-item ${chat.id === currentChatId ? 'active' : ''}`;
                item.onclick = () => switchChat(chat.id);

                item.innerHTML = `
                    <span style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:150px;">${escapeHtml(chat.name)}</span>
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
            const newChat = { id: Date.now().toString(), name: `Новый чат ${chats.length + 1}`, messages: [] };
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
                    <svg class="welcome-avatar-svg" viewBox="0 0 100 100" fill="none" xmlns="http://www.w3.org/2000/svg">
                        <path d="M50 10 L85 35 L50 90 L15 35 Z" stroke="url(#rubyGrad)" stroke-width="4" fill="none" />
                        <path d="M15 35 L85 35 M50 10 L32 35 M50 10 L68 35 M50 90 L32 35 M50 90 L68 35" stroke="url(#rubyGrad)" stroke-width="2.5" opacity="0.7" />
                        <circle cx="50" cy="48" r="14" fill="#ff4b4b" opacity="0.25" />
                        <path d="M42 45 Q46 40 50 45 Q54 40 58 45 Q60 52 50 56 Q40 52 42 45 Z" stroke="#ffffff" stroke-width="2.5" fill="none" />
                    </svg>
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
                    if (msg.file) content += `<div style="font-size:11px; opacity:0.8;">📷 ${escapeHtml(msg.file)}</div>`;
                    content += escapeHtml(msg.text);
                    box.innerHTML = content;
                    row.appendChild(box);
                } else if (msg.role === 'cancelled') {
                    const box = document.createElement('div');
                    box.className = 'msg-user';
                    box.innerHTML = escapeHtml(msg.text) + '<div style="font-size:10px; opacity:0.6; margin-top:4px; font-style:italic;">сообщение отменено</div>';
                    row.appendChild(box);
                } else {
                    const box = document.createElement('div');
                    box.className = 'msg-bot';
                    box.innerHTML = msg.text.includes('<img') ? msg.text : marked.parse(msg.text);
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

            activeChat.messages.push({ role: 'user', text: text, file: selectedFile ? selectedFile.name : null });
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
            botRow.innerHTML = `<div class="msg-bot">Думаю...</div>`;
            chatContainer.appendChild(botRow);
            chatContainer.scrollTop = chatContainer.scrollHeight;

            activeController = new AbortController();

            try {
                const res = await fetch('/api/chat', { method: 'POST', body: formData, signal: activeController.signal });
                const data = await res.json();
                document.getElementById('temp-loader-row')?.remove();
                setGeneratingState(false);
                activeController = null;

                if (res.ok) activeChat.messages.push({ role: 'bot', text: data.response });
                else activeChat.messages.push({ role: 'bot', text: 'Ошибка: ' + (data.detail || 'Не удалось получить ответ.') });
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
async def get_chat_ui():
    return HTML_TEMPLATE

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port)
