import os
import json
import redis
import tempfile
import logging
import aiohttp
import asyncio
import psycopg2
from quart import Quart, request, jsonify
from edge_tts import Communicate
import anthropic
from dotenv import load_dotenv
from uuid import uuid4
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from httpx import Timeout
import re, json
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

# Токены из переменных окружения
BLOB_READ_WRITE_TOKEN = os.getenv("BLOB_READ_WRITE_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")  # ID чата, куда будут отправляться ошибки

# Инициализация Telegram бота и диспетчера (aiogram 3.x)
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Функция для отправки ошибок в Telegram
async def send_error_to_telegram(error_message: str):
    try:
        await bot.send_message(ADMIN_CHAT_ID, f"Произошла ошибка: {error_message}")
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение об ошибке в Telegram: {e}")

# Инициализация клиента Redis
KV_URL = os.getenv("REDIS")
redis_client = redis.StrictRedis.from_url(KV_URL, socket_timeout=60, socket_connect_timeout=150)

# Настройки базы данных
DB_URL = os.getenv("DATABASE_URL")
db_connection = psycopg2.connect(DB_URL)
db_cursor = db_connection.cursor()

# Создание таблицы, если её нет
try:
    db_cursor.execute(""" 
    CREATE TABLE IF NOT EXISTS books (
        id SERIAL PRIMARY KEY,
        query TEXT UNIQUE,
        file_url TEXT,
        summary_text TEXT,
        title TEXT,
        author TEXT,
        status TEXT
    );
    """)
    db_connection.commit()
except Exception as e:
    logger.error(f"Error initializing database: {e}")
    # Асинхронную функцию нельзя вызвать здесь напрямую, поэтому только логируем ошибку

# Инициализация клиентов
http_timeout = Timeout(300.0, connect=300.0)  # 10 секунд общий таймаут, 5 секунд таймаут соединения
anthropic_client = anthropic.Client(api_key=ANTHROPIC_API_KEY, timeout=http_timeout)

# Асинхронная функция для загрузки файла в Vercel Blob Storage
async def upload_to_vercel_blob(path, data):
    headers = {
        "Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}",
        "Content-Type": "application/octet-stream",
        "x-api-version": "7",
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        async with session.put(f"https://blob.vercel-storage.com/{path}", headers=headers, data=data) as response:
            if response.status == 200:
                return (await response.json())["url"]
            else:
                raise Exception(f"Failed to upload file: {response.status}")


# Асинхронная функция для генерации аудиофайла из текста
async def generate_audio(text, model="en-US-GuyNeural"):
    communicate = Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False) as temp_audio_file:
        temp_audio_file_name = temp_audio_file.name
        await communicate.save(temp_audio_file_name)

    with open(temp_audio_file_name, "rb") as audio_file:
        audio_content = audio_file.read()

    os.remove(temp_audio_file_name)
    return audio_content

# Асинхронная функция для генерации аудиокниги
async def generate_audio_book_async(task_id, query, use_mock=False):
    try:
        # Чтение системного сообщения
        with open('system_message.txt', 'r', encoding='utf-8') as file:
            system_message = file.read()

        if use_mock:
            logging.debug("Используем локальный файл вместо запроса к API.")
            try:
                # Непосредственно читаем файл mock_response.json
                with open("mock_response.json", "r", encoding='utf-8') as f:
                    raw_content = f.read()
                
                logging.debug("Пытаемся распарсить mock_response.json с помощью safe_json_loads")
                
                # Используем безопасный парсер вместо обычной очистки и json.loads
                response_data = safe_json_loads(raw_content)
                logging.debug("Данные из mock_response.json успешно загружены")
                
            except json.JSONDecodeError as e:
                logging.error(f"Ошибка при парсинге JSON: {str(e)}")
                raise
            except Exception as e:
                logging.error(f"Ошибка при чтении файла mock_response.json: {str(e)}")
                raise
        else:
                # Вызов API Anthropic
                query_content = f"Please summarize the following query: {query}."
                message = await asyncio.wait_for(
                    asyncio.to_thread(
                        anthropic_client.messages.create,
                        model="claude-3-7-sonnet-20250219",
                        max_tokens=6195,
                        temperature=1,
                
                        system=system_message,
                        messages=[{"role": "user", "content": query_content}]
                    ),
                    timeout=60  # 1 минута
                )
                logging.debug(f"Received response from Anthropic API: {message}")
                
                # Получаем текст из ответа API
                raw_text = message.content[0].text if message.content and len(message.content) > 0 else None
                if raw_text is None:
                    raise ValueError(f"Ответ от API для запроса '{query}' не содержит текста.")
                
                # Записываем исходный ответ в файл для отладки
                response_filename = f"response_{task_id}.txt"
                with open(response_filename, "w", encoding="utf-8") as f:
                    f.write(f"ЗАПРОС: {query}\n\n")
                    f.write(f"ОТВЕТ API:\n{raw_text}")
                logging.info(f"Ответ API сохранен в файл {response_filename}")
                
                logging.debug(f"Получен текст длиной {len(raw_text)} символов")
                
                try:
                    # Используем безопасный парсер JSON
                    response_data = safe_json_loads(raw_text)
                    logging.debug("JSON успешно распарсен с помощью safe_json_loads")
                    
                    # # Записываем распарсенный JSON в файл для отладки
                    # parsed_filename = f"parsed_{task_id}.json"
                    # with open(parsed_filename, "w", encoding="utf-8") as f:
                    #     json.dump(response_data, f, ensure_ascii=False, indent=2)
                    # logging.info(f"Распарсенный JSON сохранен в файл {parsed_filename}")
                
                except Exception as e:
                    logging.error(f"Не удалось распарсить ответ от Anthropic API: {e}")
                    logging.error(f"Полный ответ API сохранен в {response_filename}")
                    raise ValueError(f"Не удалось обработать ответ API: {e}")

        if not response_data.get('summary_possible', False):
            error_message = response_data.get('summary_text', 'Не удалось сгенерировать резюме.')
            logging.error(f"Ошибка при обработке запроса '{query}': {error_message}")
            
            # Обновляем статус в базе данных
            db_cursor.execute("""
                UPDATE books 
                SET status = 'failed', summary_text = %s 
                WHERE query = %s
            """, (error_message, query))
            db_connection.commit()
            return

        summary_text = response_data.get('summary_text')
        title = response_data.get('title')
        author = response_data.get('author')
        
        logging.info(f"Сводка: {summary_text[:100]}..., Название: {title}, Автор: {author}")

        # Генерируем аудио из текста
        audio_content = await generate_audio(summary_text)
        
        # Загружаем аудио в Vercel Blob Storage
        file_path = f"audiobooks/{task_id}.mp3"
        file_url = await upload_to_vercel_blob(file_path, audio_content)

        # Обновляем запись в базе данных
        db_cursor.execute("""
            UPDATE books 
            SET status = 'completed', 
                file_url = %s, 
                summary_text = %s,
                title = %s,
                author = %s
            WHERE query = %s
        """, (file_url, summary_text, title, author, query))
        db_connection.commit()

        # Отправляем уведомление пользователю через бота
        await bot.send_message(ADMIN_CHAT_ID, f"Аудиокнига готова!\nНазвание: {title}\nАвтор: {author}\nURL: {file_url}")

    except Exception as e:
        error_message = str(e)
        logging.error(f"Ошибка при генерации аудиокниги: {error_message}")
        
        # Обновляем статус в базе данных при ошибке
        db_cursor.execute("""
            UPDATE books 
            SET status = 'failed', 
                summary_text = %s 
            WHERE query = %s
        """, (error_message, query))
        db_connection.commit()
        
        # Отправляем уведомление об ошибке
        await send_error_to_telegram(f"Ошибка при генерации аудиокниги для запроса '{query}': {error_message}")

# Регистрация обработчиков сообщений с явным указанием диспетчера (aiogram 3.x)
@dp.message(Command("start", "help"))
async def send_welcome(message: types.Message):
    await message.reply("Привет! Отправь мне название книги, и я создам аудиокнигу.")

@dp.message()
async def handle_message(message: types.Message):
    query = message.text.strip()

    # Проверяем базу данных
    db_cursor.execute("SELECT file_url, status FROM books WHERE query = %s", (query,))
    result = db_cursor.fetchone()

    if result:
        file_url, status = result
        if status == "completed":
            await message.reply(f"Ваша аудиокнига готова: {file_url}")
            return
        elif status == "pending":
            await message.reply("Ваша задача ещё в процессе. Пожалуйста, подождите.")
            return

    # Генерация уникального task_id
    task_id = str(uuid4())

    # Сохранение задачи в базу данных
    db_cursor.execute(""" 
    INSERT INTO books (query, status) 
    VALUES (%s, %s) 
    ON CONFLICT (query) DO UPDATE SET 
        status = EXCLUDED.status;
    """, (query, "pending"))
    db_connection.commit()

    # Запуск фоновой задачи
    asyncio.create_task(generate_audio_book_async(task_id, query))

    await message.reply(f"Запрос принят! ID задачи: {task_id}. Проверяйте статус позже.")

# Добавляем функцию для безопасного парсинга JSON
def safe_json_loads(json_str):
    """
    Функция для безопасного парсинга JSON с очисткой от всех недопустимых символов управления.
    
    Args:
        json_str (str): Строка, содержащая JSON данные
        
    Returns:
        dict: Распарсенный JSON-словарь
    """
    # Шаг 1: Обнаруживаем и заключаем JSON между фигурными скобками
    json_match = re.search(r'({.*})', json_str, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    
    # Шаг 2: Заменяем неразрывные пробелы на обычные
    json_str = json_str.replace('\u00A0', ' ')
    
    # Шаг 3: Обрабатываем переносы строк в значениях полей JSON
    # Ищем все текстовые значения и заменяем переносы строк на пробелы
    pattern = r'("(?:summary_text|title|author)":\s*")([^"]*)(")'
    
    def clean_value(match):
        key = match.group(1)
        value = match.group(2)
        ending = match.group(3)
        
        # Заменяем переносы строк на пробелы
        clean_value = value.replace('\n', ' ').replace('\r', ' ')
        
        return key + clean_value + ending
    
    json_str = re.sub(pattern, clean_value, json_str, flags=re.DOTALL)
    
    # Шаг 4: Удаляем всю строковую запись, если всё ещё невозможно парсить
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Шаг 5: Полная очистка от непечатаемых символов
        clean_str = ''.join(char if 32 <= ord(char) <= 126 else ' ' for char in json_str)
        clean_str = re.sub(r' +', ' ', clean_str)
        
        # Если строка выглядит как JSON но есть проблемы, пробуем удалить все проблемные символы
        if '{' in clean_str and '}' in clean_str:
            try:
                return json.loads(clean_str)
            except json.JSONDecodeError:
                pass
        
        # Шаг 6: Извлекаем ключевые поля с помощью регулярных выражений
        title_match = re.search(r'"title"\s*:\s*"([^"]+)"', json_str)
        author_match = re.search(r'"author"\s*:\s*"([^"]+)"', json_str)
        summary_match = re.search(r'"summary_text"\s*:\s*"([^"]+)"', json_str)
        
        # Если все ключевые поля найдены, создаем минимальный JSON
        if title_match and author_match and summary_match:
            title = title_match.group(1).replace('\n', ' ').replace('\r', ' ')
            author = author_match.group(1).replace('\n', ' ').replace('\r', ' ')
            summary = summary_match.group(1).replace('\n', ' ').replace('\r', ' ')
            
            # Удаляем все остальные недопустимые символы
            title = ''.join(char if 32 <= ord(char) <= 126 else ' ' for char in title)
            author = ''.join(char if 32 <= ord(char) <= 126 else ' ' for char in author)
            summary = ''.join(char if 32 <= ord(char) <= 126 else ' ' for char in summary)
            
            return {
                "determinable": True,
                "summary_possible": True,
                "title": title.strip(),
                "author": author.strip(),
                "summary_text": summary.strip()
            }
        
        # Если не удалось извлечь данные, выбрасываем исключение
        raise ValueError("Не удалось распарсить JSON и извлечь необходимые данные")

# Запуск асинхронных задач при запуске приложения
@app.before_serving
async def startup():
    # Подключаем диспетчер к боту и запускаем поллинг
    await dp.start_polling(bot)
    logger.info("Telegram bot started")

# Завершение работы приложения
@app.after_serving
async def shutdown():
    # Закрываем сессию бота
    session = await bot.get_session()
    if session:
        await session.close()
    # Останавливаем диспетчер
    await dp.stop_polling()
    logger.info("Telegram bot stopped")

# Маршрут Quart для проверки работоспособности
@app.route("/")
async def index():
    return jsonify({"status": "ok"})

# Маршрут для запуска задачи генерации аудиокниги
@app.route("/generate", methods=["POST"])
async def generate_audio_book():
    try:
        data = await request.get_json()
        query = data.get("query")
        use_mock = data.get("use_mock", False)
        
        if not query:
            return jsonify({"error": "Missing query parameter"}), 400

        # Генерируем уникальный идентификатор задачи
        task_id = str(uuid4())
        
        # Проверяем, есть ли уже такой запрос в базе данных
        db_cursor.execute("SELECT id, status, file_url FROM books WHERE query = %s", (query,))
        existing_book = db_cursor.fetchone()
        
        if existing_book:
            book_id, status, file_url = existing_book
            if status == 'completed' and file_url:
                return jsonify({
                    "status": "completed",
                    "file_url": file_url,
                    "message": "Аудиокнига уже существует"
                })
        
        # Создаем запись в базе данных
        db_cursor.execute("""
            INSERT INTO books (query, status) 
            VALUES (%s, 'processing')
            ON CONFLICT (query) DO UPDATE SET status = 'processing'
            RETURNING id
        """, (query,))
        db_connection.commit()
        
        # Создаем задачу в фоновом режиме
        asyncio.create_task(generate_audio_book_async(task_id, query, use_mock))
        
        return jsonify({
            "status": "processing",
            "task_id": task_id,
            "message": "Задача на генерацию аудиокниги поставлена в очередь"
        })
    
    except Exception as e:
        error_message = str(e)
        logging.error(f"Ошибка при обработке запроса: {error_message}")
        await send_error_to_telegram(f"Ошибка при обработке веб-запроса: {error_message}")
        return jsonify({"error": error_message}), 500

# Маршрут для проверки статуса задачи
@app.route("/status", methods=["GET"])
async def check_status():
    query = request.args.get("query")
    
    if not query:
        return jsonify({"error": "Missing query parameter"}), 400
    
    db_cursor.execute("SELECT status, file_url, summary_text, title, author FROM books WHERE query = %s", (query,))
    result = db_cursor.fetchone()
    
    if not result:
        return jsonify({"status": "not_found"}), 404
    
    status, file_url, summary_text, title, author = result
    
    response = {
        "status": status,
    }
    
    if status == "completed":
        response.update({
            "file_url": file_url,
            "summary_text": summary_text,
            "title": title,
            "author": author
        })
    elif status == "failed":
        response["error"] = summary_text
    
    return jsonify(response)

# Основная функция для запуска приложения
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
