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
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Инициализация Quart
app = Quart(__name__)

# Инициализация синхронного клиента Redis
KV_URL = "redis://default:xRSN7rzlNt194fAi1qWUbmLCg3rSFUmy@redis-10632.c323.us-east-1-2.ec2.redns.redis-cloud.com:10632"
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
    logger.debug(f"Starting upload to Vercel Blob Storage: {path}")
    async with aiohttp.ClientSession() as session:
        async with session.put(f"https://blob.vercel-storage.com/{path}", headers=headers, data=data) as response:
            if response.status == 200:
                logger.debug(f"File uploaded successfully to Vercel Blob Storage: {path}")
                return (await response.json())["url"]
            else:
                error_text = await response.text()
                logger.error(f"Failed to upload file to Vercel Blob Storage: {error_text}")
                raise Exception(f"Failed to upload file to Vercel Blob Storage: {response.status} - {error_text}")

# Функция генерации аудиофайла из текста
async def generate_audio(text, model="en-US-GuyNeural"):
    logger.debug(f"Generating audio for text: {text[:100]}...")  # Ограничиваем длину текста в логе
    communicate = Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False) as temp_audio_file:
        temp_audio_file_name = temp_audio_file.name
        await communicate.save(temp_audio_file_name)

    with open(temp_audio_file_name, "rb") as audio_file:
        audio_content = audio_file.read()

    os.remove(temp_audio_file_name)
    logger.debug(f"Audio generated and saved temporarily: {temp_audio_file_name}")
    return audio_content

# Кэширование в Redis (синхронное)
def cache_set(key, value, ttl=86400):
    logger.debug(f"Setting cache for key: {key}")
    redis_client.setex(key, ttl, json.dumps(value))

def cache_get(key):
    logger.debug(f"Getting cache for key: {key}")
    data = redis_client.get(key)
    if data:
        logger.debug(f"Cache hit for key: {key}")
        return json.loads(data)
    logger.debug(f"Cache miss for key: {key}")
    return None

# Функция для генерации ключа для кэша
def generate_cache_key(query):
    logger.debug(f"Generating cache key for query: {query}")
    return f"query:{query.lower().replace(' ', '_')}"

# Основная функция обработки запросов с кэшированием
async def generate_audio_book(body):
    try:
        query = body.get('query')

        if not query:
            logger.error("No query provided in request")
            return {"error": "Please provide a book title or author"}, 400

        # Генерация ключа для кэша
        cache_key = generate_cache_key(query)

        # Проверяем кэш для текста и аудиофайла
        cached_data = cache_get(cache_key)
        if cached_data:
            logger.debug(f"Returning cached data for query: {query}")
            return {"file_url": cached_data['file_url'], "summary_text": cached_data['summary_text']}, 200

        # Чтение системного сообщения из текстового файла
        system_message = ""
        try:
            with open('system_message.txt', 'r', encoding='utf-8') as file:
                system_message = file.read()
                logger.debug("System message loaded from file")
        except Exception as e:
            logger.error(f"Failed to load system message: {e}")
            return {"error": "Failed to load system message"}, 500

        # Генерация текста через Anthropic
        query_content = f"Please summarize the following query: {query}."
        logger.debug(f"Sending request to Anthropic API: {query_content}")
        
        message = await asyncio.to_thread(
            anthropic_client.messages.create,
            model="claude-3-haiku-20240307",
            max_tokens=4096,
            temperature=1,
            system=system_message,  # Используем системное сообщение
            messages=[{"role": "user", "content": query_content}]
        )

        # Проверяем формат ответа от API
        if not isinstance(message.content, list) or len(message.content) == 0:
            logger.error("No content returned from API")
            return {"error": "Invalid response from API"}, 500

      
        raw_text = message.content[0].text
        response_data = json.loads(raw_text, strict=False)
        # Проверяем ключевые поля в ответе
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

        logger.debug(f"Generated summary for title: {title}, author: {author}, text: {summary_text[:100]}...")  # Ограничиваем длину текста в логе

        # Генерация аудио
        audio_content = await generate_audio(summary_text)

        # Сохранение аудиофайла в Blob Storage
        filename = f"{query.lower().replace(' ', '_')}.mp3"
        logger.debug(f"Uploading audio to Vercel Blob Storage: {filename}")
        file_url = await upload_to_vercel_blob(filename, audio_content)

        logger.debug(f"Audio uploaded successfully: {file_url}")

        # Сохраняем данные в кэш
        cache_set(cache_key, {
            "file_url": file_url,
            "summary_text": summary_text
        })

        logger.debug(f"Returning response for query: {query}")
        return {"file_url": file_url, "summary_text": summary_text, "title": title, "author": author}, 201

    except Exception as e:
        logger.error(f"Error while processing query: {query}, Error: {e}")
        return {"error": str(e)}, 500

@app.route('/generate-audio-book', methods=['POST'])
async def handle_request():
    body = await request.get_json()  # Получаем тело запроса как JSON
    logger.debug(f"Received request with body: {body}")
    response_data, status_code = await generate_audio_book(body)
    return jsonify(response_data), status_code

if __name__ == '__main__':
    logger.debug("Starting the Quart app")
    app.run(debug=True, host='0.0.0.0', port=5000)
