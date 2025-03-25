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
from uuid import uuid4

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Инициализация Quart
app = Quart(__name__)

# Инициализация клиента Redis
KV_URL = os.getenv("REDIS_URL")  # URL для Redis из переменных окружения
redis_client = redis.StrictRedis.from_url(KV_URL)

# Токены из переменных окружения
BLOB_READ_WRITE_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Инициализация клиента Anthropic
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
                logger.error(f"Failed to upload file to Vercel Blob Storage: {error_text}")
                raise Exception(f"Failed to upload file to Vercel Blob Storage: {response.status} - {error_text}")

# Асинхронная функция для генерации аудиокниги
async def generate_audio_book_async(task_id, query):
    try:
        logger.info(f"Starting task {task_id} for query: {query}")
        
        # Чтение системного сообщения из файла
        system_message = ""
        try:
            with open('system_message.txt', 'r', encoding='utf-8') as file:
                system_message = file.read()
        except Exception as e:
            logger.error(f"Failed to load system message: {e}")
            redis_client.set(task_id, json.dumps({"status": "failed", "error": "System message not loaded"}), ex=86400)
            return

        # Запрос к Anthropic API для получения текста
        query_content = f"Please summarize the following query: {query}."
        message = await asyncio.to_thread(
            anthropic_client.messages.create,
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            temperature=1,
            system=system_message,
            messages=[{"role": "user", "content": query_content}]
        )

        logger.info(f"Received response from Anthropic API for task {task_id}")

        # Обработка текста из ответа
        if not isinstance(message.content, list) or len(message.content) == 0:
            logger.error(f"Invalid response from API for task {task_id}")
            redis_client.set(task_id, json.dumps({"status": "failed", "error": "Invalid response from API"}), ex=86400)
            return

        raw_text = message.content[0].text
        response_data = json.loads(raw_text, strict=False)

        # Проверяем ключевые поля в ответе
        if not response_data.get('determinable') or not response_data.get('summary_possible'):
            logger.error(f"Summary could not be generated for task {task_id}")
            redis_client.set(task_id, json.dumps({
                "status": "failed",
                "error": "Summary could not be generated",
                "determinable": response_data.get('determinable'),
                "summary_possible": response_data.get('summary_possible'),
                "title": response_data.get('title'),
                "author": response_data.get('author')
            }), ex=86400)
            return

        summary_text = response_data.get('summary_text')
        title = response_data.get('title')
        author = response_data.get('author')

        logger.info(f"Processing response for task {task_id}")

        # Генерация аудио
        audio_content = await generate_audio(summary_text)
        logger.info(f"Audio generated successfully for task {task_id}")

        # Сохранение аудиофайла в Blob Storage
        filename = f"{query.lower().replace(' ', '_')}.mp3"
        file_url = await upload_to_vercel_blob(filename, audio_content)

        logger.info(f"Audio uploaded successfully to Vercel Blob Storage for task {task_id}")

        # Сохраняем результат в Redis
        book_data = json.dumps({
            "status": "completed",
            "file_url": file_url,
            "summary_text": summary_text,
            "title": title,
            "author": author
        })
        redis_client.set(query, book_data, ex=86400)  # Кэшируем результат
        redis_client.set(task_id, book_data, ex=86400)

        logger.info(f"Task {task_id} completed successfully.")

    except Exception as e:
        redis_client.set(task_id, json.dumps({"status": "failed", "error": str(e)}), ex=86400)
        logger.error(f"Task {task_id} failed with error: {e}")

# Функция для генерации аудиофайла из текста
async def generate_audio(text, model="en-US-GuyNeural"):
    communicate = Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False) as temp_audio_file:
        temp_audio_file_name = temp_audio_file.name
        await communicate.save(temp_audio_file_name)

    with open(temp_audio_file_name, "rb") as audio_file:
        audio_content = audio_file.read()

    os.remove(temp_audio_file_name)
    return audio_content

# Роут для создания задачи генерации аудиокниги
@app.route('/generate-audio-book', methods=['POST'])
async def generate_audio_book():
    body = await request.get_json()
    query = body.get('query')

    if not query:
        return jsonify({"error": "Please provide a book title or author"}), 400

    # Проверяем наличие книги в кэше Redis по query
    cached_result = redis_client.get(query)
    if cached_result:
        logger.info(f"Returning cached result for query: {query}")
        return jsonify(json.loads(cached_result)), 200

    # Генерация уникального task_id
    task_id = str(uuid4())

    # Сохраняем задачу с начальным статусом
    redis_client.set(task_id, json.dumps({"status": "pending"}), ex=86400)

    # Запускаем задачу в фоне
    asyncio.create_task(generate_audio_book_async(task_id, query))
    logger.info(f"Task {task_id} started and set to 'pending'")

    # Возвращаем task_id клиенту сразу
    return jsonify({"task_id": task_id, "status": "pending"}), 202

# Роут для проверки статуса задачи
@app.route('/task-status/<task_id>', methods=['GET'])
async def check_task_status(task_id):
    task_status = redis_client.get(task_id)
    if task_status:
        return jsonify(json.loads(task_status)), 200
    else:
        return jsonify({"error": "Task not found"}), 404

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
