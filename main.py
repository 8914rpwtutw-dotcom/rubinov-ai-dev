import base64
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Rubinov AI API")

# Разрешаем CORS на случай запуска фронтенда и бэкенда на разных портах
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Модель входящих данных от интерфейса
class ChatRequest(BaseModel):
    prompt: Optional[str] = ""
    image: Optional[str] = None  # Base64 строка изображения, если прикреплено


@app.post("/api/chat")
async def chat_endpoint(request: ChatRequest):
    prompt_text = request.prompt.strip() if request.prompt else ""
    
    # 1. Если пользователь попросил сгенерировать картинку (нажал кнопкy ✦ или написал "Нарисуй:")
    if prompt_text.lower().startswith("нарисуй:") or prompt_text.lower().startswith("draw:"):
        clean_prompt = prompt_text.split(":", 1)[1].strip()
        
        # TODO: Вставьте здесь реальную генерацию через Imagen / DALL-E / Stable Diffusion
        # Например, получение картинки и возврат URL или Base64
        return {
            "response": f"Изображение по запросу «{clean_prompt}» успешно сгенерировано!",
            "image_url": "https://picsum.photos/800/600" # Заглушка: подставьте ваш URL или Base64
        }

    # 2. Если к запросу прикреплено фото (Vision / Анализ изображений)
    if request.image:
        # request.image содержит base64 ("data:image/png;base64,...")
        return {
            "response": f"Я получил ваше изображение и текст: «{prompt_text or 'Без описания'}». Обработка изображений работает!"
        }

    # 3. Обычный текстовый запрос к нейросети
    if prompt_text:
        # TODO: Подключите здесь вашу нейросеть (Gemini API, OpenAI API и т.д.)
        ai_reply = f"Ответ Rubinov AI на ваш запрос: {prompt_text}"
        return {"response": ai_reply}

    raise HTTPException(status_code=400, detail="Пустой запрос")


# Раздаем index.html как главную страницу
@app.get("/")
async def read_index():
    return FileResponse("index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
