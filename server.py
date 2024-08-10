import os
import asyncio
import json
import logging
import requests
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
BLOB_READ_WRITE_TOKEN = "vercel_blob_rw_cMu8v3vHQAN14ESY_SBU40vPpLMnSRWD0sHHA9Ug212BCGO"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Настройка базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = "postgresql://default:TK1fxnp7NZOh@ep-little-poetry-a2krqpco.eu-central-1.aws.neon.tech:5432/verceldb?sslmode=require"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
from flask_migrate import Migrate

migrate = Migrate(app, db)

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

@app.route('/generate-audio-book', methods=['POST'])
def generate_audio_book():
    data = request.json
    book_title = data.get('title')
    book_author = data.get('author')

    if not book_title:
        return jsonify({"error": "Please provide a book title"}), 400

    try:
        # Чтение системного сообщения из файла
        with open('system_message.txt', 'r', encoding='utf-8') as file:
            system_message = file.read()

        # Инициализация клиента Anthropic с использованием API-ключа
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        message = client.messages.create(
            model="claude-3-haiku-20240307",
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

        determinable = summary_data.get('determinable')
        summary_possible = summary_data.get('summary_possible')
        title = summary_data.get('title')
        author = summary_data.get('author')
        summary_text = summary_data.get('summary_text')

        if not summary_possible:
            return jsonify({"error": "Unable to generate summary for the given book."}), 400

        # Генерация аудио на основе текста
        audio_content = generate_audio(summary_text, "en-US-GuyNeural")
        filename = secure_filename(f"{title}_{author}.mp3")
        file_url = upload_to_vercel_blob(filename, audio_content)

        # Сохранение данных в базу данных, включая название книги и автора
        new_file = File(filename=filename, url=file_url, title=title, author=author)
        db.session.add(new_file)
        db.session.commit()

        logger.debug(f"Audio generated and saved: {filename}")

        return jsonify({"file_url": file_url, "summary_file": summary_text}), 201

    except Exception as e:
        logger.error(f"Error generating audio book: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get-file-url/<filename>', methods=['GET'])
def get_file_url(filename):
    file = File.query.filter_by(filename=filename).first()
    if file:
        return jsonify({"file_url": file.url}), 200
    else:
        return jsonify({"error": "File not found"}), 404

<<<<<<< HEAD
=======
@app.route('/generate-audio-book', methods=['POST'])
def generate_audio_book():
    data = request.json
    book_title = data.get('title')
    book_author = data.get('author')

    if not book_title or not book_author:
        return jsonify({"error": "Please provide both book title and author"}), 400

    # Запрос к API Anthropic для получения резюме книги
    try:
        anthropic_request = {
            "model": "claude-v1",
            "prompt": f"Please provide a summary for the book titled '{book_title}' by {book_author}.",
            "max_tokens_to_sample": 4000
        }
        headers = {
            "x-api-key": KEY_ANTROPIC,
            "Content-Type": "application/json"
        }

        response = requests.post("https://api.anthropic.com/v1/complete", headers=headers, json=anthropic_request)
        response_data = response.json()

        summary_text = response_data.get('completion')
        if not summary_text:
            raise Exception("Failed to generate summary from Anthropic API")

        # Генерация аудио на основе полученного текста
        filename = secure_filename(f"{book_title}_{book_author}.mp3")
        audio_content = generate_audio(summary_text, "en-US-GuyNeural")
        file_url = upload_to_vercel_blob(filename, audio_content)

        # Сохранение данных о книге и аудио в базу данных
        new_file = File(
            filename=filename,
            url=file_url,
            title=book_title,
            author=book_author,
            summary_text=summary_text
        )
        db.session.add(new_file)
        db.session.commit()

        logger.debug(f"Audio book generated and saved: {filename}")

        return jsonify({"file_url": file_url, "summary_text": summary_text}), 201

    except Exception as e:
        logger.error(f"Error generating audio book: {e}")
        return jsonify({"error": str(e)}), 500

>>>>>>> parent of 9770040 (fix)
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
