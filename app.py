import os
import json
import redis
import tempfile
import logging
import aiohttp
import asyncio
from quart import Quart, request, jsonify
from edge_tts import Communicate
import anthropic
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(level=logging.INFO)  # Установим уровень логирования как INFO для продакшн среды
logger = logging.getLogger(__name__)

# Инициализация Quart
app = Quart(__name__)

# Инициализация синхронного клиента Redis
KV_URL = os.getenv("REDIS_URL")
redis_client = redis.StrictRedis.from_url(KV_URL)

BLOB_READ_WRITE_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# Асинхронная функция для загрузки файла в Vercel Blob Storage
async def upload_to_vercel_blob(path, data):
    headers = {
        "Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}",
        "Content-Type": "application/octet-stream",
        "x-api-version": "7",
    }
    async with aiohttp.ClientSession() as session:
        async with session.put(f"https://blob.vercel-storage.com/{path}", headers=headers, data=data) as response:
            if response.status == 200:
                return (await response.json())["url"]
            else:
                error_text = await response.text()
                raise Exception(f"Failed to upload file to Vercel Blob Storage: {response.status} - {error_text}")

# Функция генерации аудиофайла из текста
async def generate_audio(text, model="en-US-GuyNeural"):
    communicate = Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False) as temp_audio_file:
        temp_audio_file_name = temp_audio_file.name
        await communicate.save(temp_audio_file_name)

    with open(temp_audio_file_name, "rb") as audio_file:
        audio_content = audio_file.read()

    os.remove(temp_audio_file_name)
    return audio_content

# Кэширование в Redis (синхронное)
def cache_set(key, value, ttl=86400):
    redis_client.setex(key, ttl, json.dumps(value))

def cache_get(key):
    data = redis_client.get(key)
    if data:
        return json.loads(data)
    return None

# Функция для генерации ключа для кэша
def generate_cache_key(query):
    return f"query:{query.lower().replace(' ', '_')}"

# Основная функция обработки запросов с кэшированием
async def generate_audio_book(body):
    try:
        query = body.get('query')

        if not query:
            return {"error": "Please provide a book title or author"}, 400

        # Генерация ключа для кэша
        cache_key = generate_cache_key(query)

        # Проверяем кэш для текста и аудиофайла
        cached_data = cache_get(cache_key)
        if cached_data:
            return {"file_url": cached_data['file_url'], "summary_text": cached_data['summary_text']}, 200

        # Чтение системного сообщения из текстового файла
        system_message = ""
        try:
            with open('system_message.txt', 'r', encoding='utf-8') as file:
                system_message = file.read()
        except Exception as e:
            return {"error": "Failed to load system message"}, 500

        # Генерация текста через Anthropic
        query_content = f"Please summarize the following query: {query}."
        
        message = await asyncio.to_thread(
            anthropic_client.messages.create,
            model="claude-3-5-sonnet-20240620",
            max_tokens=4096,
            temperature=1,
            system=system_message,
            messages=[{"role": "user", "content": query_content}]
        )

        # Проверка формата ответа
        if not isinstance(message.content, list) or len(message.content) == 0:
            return {"error": "Invalid response from API"}, 500

        raw_text = message.content[0].text
        response_data = json.loads(raw_text, strict=False)

        # Проверка ключевых полей
        if not response_data.get('determinable') or not response_data.get('summary_possible'):
            return {
                "error": "Summary could not be generated for the given query",
                "determinable": response_data.get('determinable'),
                "summary_possible": response_data.get('summary_possible'),
                "title": response_data.get('title'),
                "author": response_data.get('author')
            }, 400

        summary_text = response_data.get('summary_text')
        title = response_data.get('title')
        author = response_data.get('author')

        # Генерация аудио
        audio_content = await generate_audio(summary_text)

        # Сохранение аудиофайла в Blob Storage
        filename = f"{query.lower().replace(' ', '_')}.mp3"
        file_url = await upload_to_vercel_blob(filename, audio_content)

        # Сохраняем данные в кэш
        cache_set(cache_key, {
            "file_url": file_url,
            "summary_text": summary_text
        })

        return {"file_url": file_url, "summary_text": summary_text, "title": title, "author": author}, 201

    except Exception as e:
        return {"error": str(e)}, 500

@app.route('/generate-audio-book', methods=['POST'])
async def handle_request():
    body = await request.get_json()
    response_data, status_code = await generate_audio_book(body)
    return jsonify(response_data), status_code

# Это основной обработчик, который запускается Vercel
@app.route('/', methods=['GET'])
async def index():
    return jsonify({"status": "Server is running"}), 200

# Это для локальной разработки
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
