import os
import asyncio
import json
import logging
import aiohttp
import tempfile
from flask import Flask, request, jsonify
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import edge_tts
import anthropic

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
app.config['DEBUG'] = True

# Настройка Vercel Blob Storage
BLOB_READ_WRITE_TOKEN = os.environ.get("BLOB_READ_WRITE_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Настройка базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = "postgresql://default:TK1fxnp7NZOh@ep-little-poetry-a2krqpco.eu-central-1.aws.neon.tech:5432/verceldb?sslmode=require"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Модель базы данных для хранения метаданных файлов
class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    url = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, nullable=True)
    title = db.Column(db.String(255), nullable=False)  # Название книги
    author = db.Column(db.String(255), nullable=False)  # Автор книги

# Настройки приложения
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp3'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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

@app.route('/', methods=['GET'])
async def home():
    return jsonify({"status": "Server is running"}), 200

@app.route('/generate-audio-book', methods=['POST'])
async def generate_audio_book():
    data = await request.json
    book_title = data.get('title')
    book_author = data.get('author')

    if not book_title:
        return jsonify({"error": "Please provide a book title"}), 400

    try:
        # Чтение системного сообщения из файла
        with open('system_message.txt', 'r', encoding='utf-8') as file:
            system_message = file.read()

        # Инициализация клиента Anthropic с использованием API-ключа
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=4096,
            temperature=1,
            system=system_message,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{book_title}"
                        }
                    ]
                }
            ]
        )

        raw_text = message.content[0].text
        summary_data = json.loads(raw_text, strict=False)

        if not summary_data.get('summary_possible'):
            return jsonify({"error": "Unable to generate summary for the given book."}), 400

        summary_text = summary_data.get('summary_text')

        # Генерация аудио на основе текста
        audio_content = await generate_audio(summary_text, "en-US-GuyNeural")
        filename = secure_filename(f"{book_title}_{book_author}.mp3")
        file_url = await upload_to_vercel_blob(filename, audio_content)

        # Сохранение данных в базу данных, включая название книги и автора
        new_file = File(filename=filename, url=file_url, title=book_title, author=book_author)
        db.session.add(new_file)
        db.session.commit()

        logger.debug(f"Audio generated and saved: {filename}")

        return jsonify({"file_url": file_url, "summary_file": summary_text}), 201

    except Exception as e:
        logger.error(f"Error generating audio book: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get-file-url/<filename>', methods=['GET'])
async def get_file_url(filename):
    file = File.query.filter_by(filename=filename).first()
    if file:
        return jsonify({"file_url": file.url}), 200
    else:
        return jsonify({"error": "File not found"}), 404

async def generate_audio(text, model):
    communicate = edge_tts.Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False) as temp_audio_file:
        temp_audio_file_name = temp_audio_file.name
        await communicate.save(temp_audio_file_name)
    
    with open(temp_audio_file_name, "rb") as audio_file:
        audio_content = audio_file.read()

    os.remove(temp_audio_file_name)
    return audio_content

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Создание таблиц в базе данных

    app.run(host='0.0.0.0', port=5000)
