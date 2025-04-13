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
from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from httpx import Timeout
import re, json
import time
# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Получаем переменные окружения
BASE_URL = os.getenv("BASE_URL")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{BASE_URL}{WEBHOOK_PATH}"

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
@app.route(f"/{TELEGRAM_TOKEN}", methods=["POST"])
async def webhook():
    try:
        update = types.Update(**(await request.get_json()))
        logger.info(f"Получен вебхук: {update}")

        # Проверяем, есть ли сообщение
        if not update.message:
            logger.info("Обновление не содержит сообщения, игнорируем")
            return "", 200

        message = update.message
        chat_id = message.chat.id
        query = None

        # Обработка команд
        if message.text:
            if message.text == '/start':
                await bot.send_message(chat_id, "Привет! Я бот для генерации аудиокниг. Просто отправь мне название книги или запрос для поиска, и я сгенерирую для тебя аудиокнигу.")
                return "", 200
            elif message.text == '/help':
                await bot.send_message(chat_id, "Для генерации аудиокниги просто отправь мне запрос. Например: 'Рассказы о Шерлоке Холмсе' или 'История России'")
                return "", 200
            else:
                query = message.text

        # Если нет запроса, сообщаем об ошибке
        if not query:
            await bot.send_message(chat_id, "Пожалуйста, отправьте текстовый запрос для генерации аудиокниги.")
            return "", 200

        # Проверяем, не обрабатывается ли уже такой запрос
        db_cursor.execute("SELECT status FROM books WHERE query = %s", (query,))
        result = db_cursor.fetchone()
        
        if result:
            status = result[0]
            if status in ['processing', 'completed']:
                await bot.send_message(chat_id, f"Запрос '{query}' уже {status}. Пожалуйста, дождитесь завершения или проверьте результаты.")
                return "", 200
        
        # Создаем уникальный ID для задачи
        task_id = str(uuid4())
        
        # Сохраняем задачу в Redis со сроком хранения 24 часа
        task_data = {
            "query": query,
            "chat_id": chat_id,
            "created_at": time.time(),
            "status": "pending"
        }
        redis_client.set(f"task:{task_id}", json.dumps(task_data), ex=86400)
        
        # Добавляем задачу в очередь
        redis_client.lpush("audio_book_tasks", task_id)
        
        # Логируем
        logger.info(f"Задача {task_id} добавлена в очередь для запроса '{query}'")
        
        # Записываем в базу данных
        db_cursor.execute("""
            INSERT INTO books (query, chat_id, status) 
            VALUES (%s, %s, %s)
            ON CONFLICT (query) DO UPDATE 
            SET status = 'pending', chat_id = %s
        """, (query, chat_id, 'pending', chat_id))
        db_connection.commit()
        
        # Отправляем сообщение пользователю
        await bot.send_message(chat_id, f"Ваш запрос '{query}' добавлен в очередь. Я сообщу, когда аудиокнига будет готова.")
        
        # Асинхронно запускаем обработку задачи
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{BASE_URL}/process-next-task", 
                                       json={"secret": os.getenv("WEBHOOK_SECRET", "")}, 
                                       timeout=2) as response:
                    logger.info(f"Запрос на обработку отправлен, статус: {response.status}")
            except Exception as e:
                logger.error(f"Не удалось запустить обработку: {str(e)}")
                # Не ломаем вебхук, если не удалось запустить обработку
                pass
        
        return "", 200
    except Exception as e:
        error_message = f"Ошибка в обработчике webhook: {str(e)}"
        logger.error(error_message, exc_info=True)
        return jsonify({"error": error_message}), 500

async def generate_audio_book_async(task_id, query, use_mock=False):
    """
    Асинхронная функция для генерации аудиокниги
    """
    try:
        # Получаем данные задачи из Redis
        task_data_str = redis_client.get(f"task:{task_id}")
        if not task_data_str:
            logger.error(f"Задача {task_id} не найдена в Redis")
            return
        
        task_data = json.loads(task_data_str)
        chat_id = task_data.get("chat_id")
        
        if not chat_id:
            logger.error(f"Задача {task_id} не содержит chat_id")
            return
        
        # Сообщаем о начале генерации
        await bot.send_message(chat_id, f"Начинаю генерацию аудиокниги для запроса '{query}'")
        
        # Обновляем статус
        logger.info(f"Начинаем генерацию контента для запроса '{query}'")
        await bot.send_message(chat_id, "🔍 Ищу информацию и генерирую содержание книги...")
        
        # Генерация содержания книги
        logger.info("Генерация содержания книги")
        if use_mock:
            book_content = f"Это тестовое содержание книги для запроса '{query}'"
        else:
            book_content = await generate_book_content(query)
        
        if not book_content:
            logger.error("Не удалось сгенерировать содержание книги")
            await bot.send_message(chat_id, "К сожалению, не удалось сгенерировать содержание книги. Пожалуйста, попробуйте другой запрос.")
            return
        
        # Обновляем статус
        logger.info("Содержание книги сгенерировано, начинаем генерацию аудио")
        await bot.send_message(chat_id, "📝 Содержание книги готово. Начинаю преобразование в аудио...")
        
        # Разбиваем контент на части
        parts = split_content(book_content)
        total_parts = len(parts)
        logger.info(f"Контент разбит на {total_parts} частей")
        
        audio_parts = []
        audio_files = []
        
        # Сохраняем первую часть содержания книги в базу данных
        db_cursor.execute("""
            UPDATE books 
            SET content = %s, parts_total = %s, parts_completed = 0
            WHERE query = %s
        """, (book_content[:1000] + "...", total_parts, query))
        db_connection.commit()
        
        # Обрабатываем каждую часть
        for i, part in enumerate(parts, 1):
            try:
                logger.info(f"Обработка части {i}/{total_parts}")
                
                # Обновляем статус пользователю каждые 3 части или если это первая/последняя часть
                if i == 1 or i == total_parts or i % 3 == 0:
                    progress = int((i-1) / total_parts * 100)
                    await bot.send_message(chat_id, f"🔊 Генерация аудио: {progress}% ({i-1}/{total_parts})")
                
                # Генерируем аудио для части текста
                audio_data = None
                if use_mock:
                    # Имитируем задержку
                    await asyncio.sleep(1)
                    # Для тестирования используем пустой аудио-фрагмент
                    audio_data = b"mock_audio_data"
                else:
                    start_time = time.time()
                    timeout = 25  # Ограничиваем время выполнения запроса
                    try:
                        audio_data = await asyncio.wait_for(
                            text_to_speech(part), 
                            timeout=timeout
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Превышено время ожидания ({timeout}с) при генерации аудио для части {i}")
                        # Если превышен таймаут, генерируем более короткую часть
                        short_part = truncate_text(part, 100)
                        audio_data = await asyncio.wait_for(
                            text_to_speech(f"Внимание, превышено время ожидания при генерации. Продолжаем с сокращенной версией. {short_part}"), 
                            timeout=10
                        )
                
                if not audio_data:
                    logger.error(f"Не удалось сгенерировать аудио для части {i}")
                    continue
                
                # Сохраняем аудио в оперативной памяти
                audio_parts.append(audio_data)
                
                # Обновляем прогресс в базе данных
                db_cursor.execute("""
                    UPDATE books 
                    SET parts_completed = %s
                    WHERE query = %s
                """, (i, query))
                db_connection.commit()
                
                # Обновляем статус задачи в Redis
                task_data["progress"] = i / total_parts
                redis_client.set(f"task:{task_id}", json.dumps(task_data), ex=86400)
                
            except Exception as e:
                logger.error(f"Ошибка при обработке части {i}: {str(e)}")
                # Если возникла ошибка, продолжаем с следующей частью
                continue
        
        # Объединяем все аудио-части
        logger.info("Объединение аудио-частей")
        combined_audio = None
        
        try:
            # Если используется заглушка, просто имитируем объединение
            if use_mock:
                combined_audio = b"mock_combined_audio"
                filename = f"{query.replace(' ', '_')}_mock.mp3"
            else:
                combined_audio = combine_audio_parts(audio_parts)
                filename = f"{query.replace(' ', '_')}.mp3"
            
            # Сохраняем в базу данных URL файла
            file_url = f"{BASE_URL}/audio/{filename}"
            
            # Сохраняем аудио в S3/Cloudinary или другое облачное хранилище
            # Здесь должен быть код для загрузки в облако
            
            # Обновляем запись в базе данных
            db_cursor.execute("""
                UPDATE books 
                SET status = 'completed', audio_url = %s
                WHERE query = %s
            """, (file_url, query))
            db_connection.commit()
            
            # Отправляем пользователю ссылку на аудио
            await bot.send_message(chat_id, f"✅ Аудиокнига для запроса '{query}' готова!")
            await bot.send_message(chat_id, f"Вы можете скачать её по ссылке: {file_url}")
            
            # Удаляем задачу из Redis, так как она завершена
            redis_client.delete(f"task:{task_id}")
            
            logger.info(f"Аудиокнига для запроса '{query}' успешно сгенерирована")
            return True
            
        except Exception as e:
            logger.error(f"Ошибка при финализации аудиокниги: {str(e)}")
            await bot.send_message(chat_id, f"Произошла ошибка при создании аудиокниги: {str(e)}")
            
            # Обновляем статус в базе данных
            db_cursor.execute("""
                UPDATE books 
                SET status = 'error', error = %s
                WHERE query = %s
            """, (str(e), query))
            db_connection.commit()
            return False
    
    except Exception as e:
        logger.error(f"Ошибка в generate_audio_book_async: {str(e)}", exc_info=True)
        # Отправляем уведомление об ошибке пользователю, если можем получить chat_id
        try:
            task_data_str = redis_client.get(f"task:{task_id}")
            if task_data_str:
                task_data = json.loads(task_data_str)
                chat_id = task_data.get("chat_id")
                if chat_id:
                    await bot.send_message(chat_id, f"Произошла ошибка при генерации аудиокниги: {str(e)}")
        except:
            pass
        return False

# Запуск асинхронных задач при запуске приложения
@app.before_serving
async def startup():
    # Устанавливаем webhook для бота
    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != WEBHOOK_URL:
        logger.info(f"Устанавливаем webhook на URL: {WEBHOOK_URL}")
        await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    logger.info("Telegram bot webhook настроен")

# Завершение работы приложения
@app.after_serving
async def shutdown():
    # Закрываем сессию бота
    session = await bot.get_session()
    if session:
        await session.close()
    logger.info("Telegram bot сессия закрыта")

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

# Маршрут для продолжения обработки задач, которые были прерваны из-за timeout
@app.route("/continue-processing", methods=["POST"])
async def continue_processing():
    try:
        data = await request.get_json()
        query = data.get("query")
        
        if not query:
            return jsonify({"error": "Missing query parameter"}), 400
        
        # Проверяем статус задачи
        db_cursor.execute("SELECT status FROM books WHERE query = %s", (query,))
        result = db_cursor.fetchone()
        
        if not result:
            return jsonify({"status": "not_found", "message": "Задача не найдена"}), 404
        
        status = result[0]
        
        if status == 'completed':
            return jsonify({"status": "completed", "message": "Задача уже выполнена"}), 200
        
        if status not in ['processing_timeout', 'pending', 'processing', 'summary_ready']:
            return jsonify({"status": status, "message": f"Задача в статусе {status} не может быть продолжена"}), 400
        
        # Создаем новый task_id для продолжения обработки
        task_id = str(uuid4())
        
        # Запускаем обработку в фоновом режиме
        asyncio.create_task(generate_audio_book_async(task_id, query))
        
        return jsonify({
            "status": "processing",
            "task_id": task_id,
            "message": "Задача на генерацию аудиокниги продолжена"
        })
        
    except Exception as e:
        error_message = str(e)
        logging.error(f"Ошибка при продолжении обработки: {error_message}")
        return jsonify({"error": error_message}), 500

# Маршрут для обработки следующей задачи из очереди
@app.route("/process-next-task", methods=["POST"])
async def process_next_task():
    try:
        # Проверяем секретный ключ для безопасности
        data = await request.get_json()
        secret = data.get("secret", "")
        if secret != os.getenv("WEBHOOK_SECRET", ""):
            logger.warning(f"Попытка вызова process-next-task с неправильным секретным ключом")
            return jsonify({"error": "Unauthorized"}), 401
        
        # Получаем следующую задачу из очереди
        task_id = redis_client.rpop("audio_book_tasks")
        if not task_id:
            logger.info("Очередь пуста, нет задач для обработки")
            return jsonify({"status": "no_tasks"}), 200
        
        task_id = task_id.decode('utf-8')
        task_data_str = redis_client.get(f"task:{task_id}")
        
        if not task_data_str:
            logger.error(f"Задача {task_id} не найдена в Redis")
            return jsonify({"status": "task_not_found"}), 404
        
        task_data = json.loads(task_data_str)
        query = task_data.get("query")
        chat_id = task_data.get("chat_id")
        use_mock = task_data.get("use_mock", False)
        
        if not query:
            logger.error(f"Задача {task_id} не содержит запроса")
            return jsonify({"status": "invalid_task"}), 400
        
        # Обрабатываем задачу
        logger.info(f"Начинаем обработку задачи {task_id} для запроса '{query}'")
        
        # Отмечаем в Redis, что задача в обработке
        task_data["status"] = "processing"
        redis_client.set(f"task:{task_id}", json.dumps(task_data), ex=86400)
        
        # Обновляем статус в базе данных
        db_cursor.execute("""
            UPDATE books 
            SET status = 'processing' 
            WHERE query = %s
        """, (query,))
        db_connection.commit()
        
        try:
            # Запускаем генерацию аудиокниги
            await generate_audio_book_async(task_id, query, use_mock)
            return jsonify({"status": "success"}), 200
        except Exception as e:
            logger.error(f"Ошибка при обработке задачи {task_id}: {str(e)}")
            # Отмечаем задачу как "processing_timeout", чтобы её можно было продолжить позже
            db_cursor.execute("""
                UPDATE books 
                SET status = 'processing_timeout' 
                WHERE query = %s
            """, (query,))
            db_connection.commit()
            
            # Возвращаем задачу в очередь, чтобы её можно было обработать снова
            redis_client.lpush("audio_book_tasks", task_id)
            
            # Уведомляем пользователя
            if chat_id:
                try:
                    await bot.send_message(chat_id, f"Возникла ошибка при обработке вашего запроса '{query}'. Мы попробуем ещё раз позже.")
                except Exception as msg_e:
                    logger.error(f"Не удалось отправить сообщение пользователю: {msg_e}")
            
            return jsonify({"status": "error", "message": str(e)}), 500
    
    except Exception as e:
        logger.error(f"Ошибка в обработчике process-next-task: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# Маршрут для обработки всех задач из очереди
@app.route("/process-all-tasks", methods=["POST"])
async def process_all_tasks():
    try:
        # Проверяем секретный ключ для безопасности
        data = await request.get_json()
        secret = data.get("secret", "")
        if secret != os.getenv("WEBHOOK_SECRET", ""):
            logger.warning(f"Попытка вызова process-all-tasks с неправильным секретным ключом")
            return jsonify({"error": "Unauthorized"}), 401
        
        # Получаем количество задач в очереди
        queue_size = redis_client.llen("audio_book_tasks")
        if queue_size == 0:
            return jsonify({"status": "no_tasks"}), 200
        
        # Запускаем обработку первой задачи
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BASE_URL}/process-next-task", json={"secret": os.getenv("WEBHOOK_SECRET", "")}) as response:
                logger.info(f"Запрос на обработку отправлен, статус: {response.status}")
        
        return jsonify({"status": "success", "queue_size": queue_size}), 200
    
    except Exception as e:
        logger.error(f"Ошибка в обработчике process-all-tasks: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# Маршрут для проверки статуса бота и очереди задач
@app.route("/bot-status", methods=["GET"])
async def bot_status():
    try:
        # Проверяем статус Redis
        redis_ok = False
        try:
            redis_ping = redis_client.ping()
            redis_ok = True
        except:
            redis_ok = False
        
        # Проверяем статус БД
        db_ok = False
        try:
            db_cursor.execute("SELECT 1")
            db_ok = True
        except:
            db_ok = False
        
        # Проверяем статус бота
        bot_ok = False
        try:
            webhook_info = await bot.get_webhook_info()
            bot_ok = webhook_info.url == WEBHOOK_URL
        except:
            bot_ok = False
        
        # Проверяем очередь
        queue_size = 0
        try:
            queue_size = redis_client.llen("audio_book_tasks")
        except:
            pass
        
        return jsonify({
            "status": "ok",
            "redis_ok": redis_ok,
            "db_ok": db_ok,
            "bot_ok": bot_ok,
            "queue_size": queue_size,
            "last_error": None  # Здесь можно добавить информацию о последней ошибке из Redis
        }), 200
    
    except Exception as e:
        logger.error(f"Ошибка в обработчике bot-status: {str(e)}", exc_info=True)
        return jsonify({"error": str(e)}), 500

# Основная функция для запуска приложения
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
