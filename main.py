import os
import base64
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Rubinov AI API")

# Настройки CORS для работы на продакшене
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    prompt: Optional[str] = ""
    image: Optional[str] = None

# 1. Главная страница (отдает index.html)
@app.get("/")
async def read_index():
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"error": "index.html not found on server"}

# 2. Основной API эндпоинт чата
@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    prompt_text = request.prompt.strip() if request.prompt else ""
    
    # Режим генерации картинок
    if prompt_text.lower().startswith("нарисуй:") or prompt_text.lower().startswith("draw:"):
        clean_prompt = prompt_text.split(":", 1)[1].strip()
        return {
            "response": f"Изображение по запросу «{clean_prompt}» сгенерировано!",
            "image_url": "https://picsum.photos/800/600"  # Замените на вызов вашего Imagen API
        }

    # Анализ прикрепленного фото
    if request.image:
        return {
            "response": f"Изображение получено. Текст: «{prompt_text or 'Без текста'}»"
        }

    # Обычный текстовый запрос
    if prompt_text:
        return {
            "response": f"Ответ Rubinov AI: {prompt_text}"
        }

    raise HTTPException(status_code=400, detail="Empty prompt")

# Запуск Uvicorn с учетом порта Render
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
