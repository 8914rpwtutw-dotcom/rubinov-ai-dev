import os
import time
import uuid
import base64
from typing import Optional
from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from google import genai
from google.genai import types

app = FastAPI()

SERVER_BUILD_ID = str(uuid.uuid4())[:8]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_genai_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY не найден в Environment Variables")
    return genai.Client(api_key=api_key)

@app.get("/", response_class=HTMLResponse)
def get_chat_ui():
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Rubinov AI — Beta Test</title>
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #131314; color: #e3e3e3; margin: 0; padding: 0; display: flex; flex-direction: column; height: 100vh; }}
            .header {{ padding: 12px 20px; background: #1e1f20; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #333537; }}
            .brand-container {{ display: flex; align-items: center; gap: 10px; }}
            .beta-badge {{ background: #b3261e; color: white; font-size: 11px; font-weight: bold; padding: 3px 8px; border-radius: 4px; letter-spacing: 0.5px; text-transform: uppercase; }}
            .chat-container {{ flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 15px; max-width: 800px; width: 100%; margin: 0 auto; box-sizing: border-box; }}
            .message {{ padding: 12px 16px; border-radius: 12px; max-width: 80%; line-height: 1.5; }}
            .user {{ background: #004a77; color: #e3e3e3; align-self: flex-end; }}
            .bot {{ background: #1e1f20; color: #e3e3e3; align-self: flex-start; border: 1px solid #333537; }}
            .input-container {{ padding: 20px; background: #131314; display: flex; justify-content: center; }}
            .input-box {{ max-width: 800px; width: 100%; display: flex; gap: 10px; background: #1e1f20; padding: 10px; border-radius: 24px; border: 1px solid #333537; }}
            input {{ flex: 1; background: transparent; border: none; color: #e3e3e3; font-size: 16px; outline: none; padding-left: 10px; }}
            button {{ background: #8ab4f8; color: #202124; border: none; padding: 8px 16px; border-radius: 18px; font-weight: bold; cursor: pointer; }}
            .mode-select {{ background: #2d2e30; color: white; border: none; padding: 6px 12px; border-radius: 8px; outline: none; cursor: pointer; font-size: 14px; }}
            img {{ max-width: 100%; border-radius: 8px; margin-top: 10px; }}

            #beta-popup {{
                position: fixed;
                top: 20px;
                left: 50%;
                transform: translateX(-50%);
                background: #b3261e;
                color: white;
                padding: 12px 24px;
                border-radius: 8px;
                font-weight: bold;
                box-shadow: 0 4px 15px rgba(0,0,0,0.5);
                z-index: 9999;
                animation: fadeInOut 5s forwards;
            }}
            @keyframes fadeInOut {{
                0% {{ opacity: 0; transform: translate(-50%, -20px); }}
                10% {{ opacity: 1; transform: translate(-50%, 0); }}
                80% {{ opacity: 1; transform: translate(-50%, 0); }}
                100% {{ opacity: 0; transform: translate(-50%, -20px); display: none; }}
            }}
        </style>
    </head>
    <body>
        <div id="beta-popup">⚠️ Сайт для бета-теста (Dev Environment)</div>

        <div class="header">
            <div class="brand-container">
                <b style="font-size: 18px;">Rubinov AI</b>
                <span class="beta-badge">БЕТА ТЕСТ</span>
            </div>
            <select id="modeSelect" class="mode-select">
                <option value="chat">💬 Текстовый чат</option>
                <option value="image">🎨 Генерация картинок</option>
            </select>
        </div>

        <div class="chat-container" id="chatBox">
            <div class="message bot">Привет! Это бета-версия Rubinov AI. Выберите режим работы вверху справа и отправьте запрос!</div>
        </div>

        <div class="input-container">
            <div class="input-box">
                <input type="text" id="userInput" placeholder="Введите запрос..." onkeydown="if(event.key==='Enter') sendMessage()">
                <button onclick="sendMessage()">➤</button>
            </div>
        </div>

        <script>
            setTimeout(() => {{
                const popup = document.getElementById('beta-popup');
                if (popup) popup.remove();
            }}, 5000);

            async function sendMessage() {{
                const input = document.getElementById('userInput');
                const chatBox = document.getElementById('chatBox');
                const mode = document.getElementById('modeSelect').value;
                const text = input.value.trim();
                if (!text) return;

                chatBox.innerHTML += `<div class="message user">${{text}}</div>`;
                input.value = '';
                chatBox.scrollTop = chatBox.scrollHeight;

                const loadId = 'load_' + Math.random();
                chatBox.innerHTML += `<div class="message bot" id="${{loadId}}">${{mode === 'chat' ? 'Думаю...' : 'Генерирую изображение...'}}</div>`;
                chatBox.scrollTop = chatBox.scrollHeight;

                const endpoint = mode === 'chat' ? '/chat' : '/generate-image';
                const bodyKey = mode === 'chat' ? 'message' : 'prompt';

                try {{
                    const response = await fetch(endpoint, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                        body: bodyKey + '=' + encodeURIComponent(text)
                    }});
                    const data = await response.text();
                    document.getElementById(loadId).outerHTML = `<div class="message bot">${{data}}</div>`;
                }} catch (e) {{
                    document.getElementById(loadId).outerHTML = `<div class="message bot" style="color:red;">Ошибка соединения с сервером</div>`;
                }}
                chatBox.scrollTop = chatBox.scrollHeight;
            }}
        </script>
    </body>
    </html>
    """

@app.post("/chat")
async def chat_endpoint(message: str = Form(...)):
    retries = 3
    delay = 2
    for attempt in range(retries):
        try:
            client = get_genai_client()
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=message,
            )
            return response.text
        except Exception as e:
            if "429" in str(e) or "overloaded" in str(e).lower() or "RESOURCE_EXHAUSTED" in str(e):
                if attempt < retries - 1:
                    time.sleep(delay * (attempt + 1))
                    continue
            return f"Ошибка ИИ: {str(e)}"
    return "Сервис перегружен. Повторите попытку через пару секунд."

@app.post("/generate-image")
async def generate_image_endpoint(prompt: str = Form(...)):
    try:
        client = get_genai_client()
        result = client.models.generate_images(
            model='imagen-3.0-generate-002',
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/jpeg",
                aspect_ratio="1:1",
            )
        )
        for img in result.generated_images:
            b64 = base64.b64encode(img.image.image_bytes).decode("utf-8")
            return HTMLResponse(content=f'<img src="data:image/jpeg;base64,{b64}"/><br><small>Запрос: {prompt}</small>')
        return "Не удалось создать картинку."
    except Exception as e:
        return f"Ошибка генерации: {str(e)}"
