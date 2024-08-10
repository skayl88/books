import os
import asyncio
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
import logging
import requests
import tempfile
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import edge_tts

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
app.config['DEBUG'] = True

# Получение секретов и других настроек из переменных окружения
BLOB_READ_WRITE_TOKEN = os.environ.get('BLOB_READ_WRITE_TOKEN')
KEY_ANTROPIC = os.environ.get('KEY_ANTROPIC')
DATABASE_URL = os.environ.get('DATABASE_URL')
# Настройка Vercel Blob Storage
BLOB_READ_WRITE_TOKEN = 'vercel_blob_rw_cMu8v3vHQAN14ESY_SBU40vPpLMnSRWD0sHHA9Ug212BCGO'

# Настройка базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql+psycopg2://default:DR9xJNrve5HF@ep-little-poetry-a2krqpco.eu-central-1.aws.neon.tech:5432/verceldb?sslmode=require'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Проверка наличия необходимых переменных окружения
if not BLOB_READ_WRITE_TOKEN:
    raise EnvironmentError("Missing required environment variable: BLOB_READ_WRITE_TOKEN")
if not KEY_ANTROPIC:
    raise EnvironmentError("Missing required environment variable: KEY_ANTROPIC")
if not DATABASE_URL:
    raise EnvironmentError("Missing required environment variable: DATABASE_URL")

# Настройка базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

try:
    db = SQLAlchemy(app)
except Exception as e:
    logger.error(f"Failed to initialize SQLAlchemy: {e}")
    raise

# Модель базы данных для хранения метаданных файлов и данных о книге
class BookSummary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    url = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(255), nullable=True)
    author = db.Column(db.String(255), nullable=True)
    summary_text = db.Column(db.Text, nullable=True)
    determinable = db.Column(db.Boolean, nullable=False)
    summary_possible = db.Column(db.Boolean, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Настройки приложения
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp3'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_vercel_blob(path, data):
    headers = {
        "Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}",
        "Content-Type": "application/octet-stream",
        "x-api-version": "7",
        "x-content-type": "application/octet-stream"
    }
    response = requests.put(f"https://blob.vercel-storage.com/{path}", headers=headers, data=data)
    if response.status_code == 200:
        return response.json()["url"]
    else:
        logger.error(f"Failed to upload file to Vercel Blob Storage: {response.text}")
        raise Exception(f"Failed to upload file to Vercel Blob Storage: {response.status_code} - {response.text}")

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "Server is running"}), 200

@app.route('/generate-audio-from-book', methods=['POST'])
def generate_audio_from_book():
    data = request.json
    book_title = data.get('book_title')
    author = data.get('author')

    if not book_title or not author:
        return jsonify({"error": "Please provide both book title and author"}), 400

    # Чтение системного сообщения из файла
    try:
        with open('system_message.txt', 'r') as file:
            system_message = file.read()
    except Exception as e:
        logger.error(f"Failed to read system message file: {e}")
        return jsonify({"error": "System message file could not be read"}), 500

    # Формирование запроса к API Anthropic
    prompt = f"{system_message}\n\nModel: Claude 3.5 Sonnet\nTemperature: 1\nMax tokens: 4000\n\nPlease provide a summary for the book titled '{book_title}' by {author}."
    headers = {
        "x-api-key": KEY_ANTROPIC,
        "Content-Type": "application/json"
    }
    anthropic_request = {
        "prompt": prompt,
        "model": "claude-v1",
        "max_tokens_to_sample": 4000
    }

    try:
        response = requests.post("https://api.anthropic.com/v1/complete", headers=headers, json=anthropic_request)
        response_data = response.json()

        # Извлечение данных из ответа
        determinable = response_data.get('determinable', False)
        summary_possible = response_data.get('summary_possible', False)
        title = response_data.get('title', book_title if determinable else None)
        author = response_data.get('author', author if determinable else None)
        summary_text = response_data.get('summary_text', None)

        if not summary_possible or not summary_text:
            raise Exception("Summary could not be generated from Anthropic API")

        # Генерация аудио на основе полученного текста
        audio_content = generate_audio(summary_text, "en-US-GuyNeural")
        filename = secure_filename(f"{title}_{author}.mp3")
        file_url = upload_to_vercel_blob(filename, audio_content)

        # Сохранение данных о книге и аудио в базу данных
        new_summary = BookSummary(
            filename=filename,
            url=file_url,
            title=title,
            author=author,
            summary_text=summary_text,
            determinable=determinable,
            summary_possible=summary_possible
        )
        db.session.add(new_summary)
        db.session.commit()

        logger.debug(f"Audio generated and saved: {filename}")

        return jsonify({"file_url": file_url}), 201

    except Exception as e:
        logger.error(f"Error generating audio from book title: {e}")
        return jsonify({"error": str(e)}), 500

def generate_audio(text, model):
    async def run():
        communicate = edge_tts.Communicate(text, model)
        with tempfile.NamedTemporaryFile(delete=False) as temp_audio_file:
            temp_audio_file_name = temp_audio_file.name
            await communicate.save(temp_audio_file_name)
        
        with open(temp_audio_file_name, "rb") as audio_file:
            audio_content = audio_file.read()

        os.remove(temp_audio_file_name)
        return audio_content

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    audio_content = loop.run_until_complete(run())
    loop.close()

    return audio_content

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Создание таблиц в базе данных

    app.run(host='0.0.0.0', port=5000)
