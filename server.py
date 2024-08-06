from flask import Flask, request, jsonify
import edge_tts
import os
import asyncio
from werkzeug.utils import secure_filename
import logging
import requests
import tempfile
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
app.config['DEBUG'] = True

# Настройка Vercel Blob Storage
BLOB_READ_WRITE_TOKEN = 'vercel_blob_rw_cMu8v3vHQAN14ESY_SBU40vPpLMnSRWD0sHHA9Ug212BCGO'

# Настройка базы данных
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql+psycopg2://default:DR9xJNrve5HF@ep-little-poetry-a2krqpco.eu-central-1.aws.neon.tech:5432/verceldb?sslmode=require'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

try:
    db = SQLAlchemy(app)
except Exception as e:
    logger.error(f"Failed to initialize SQLAlchemy: {e}")
    raise

# Настройка Telegram Bot
TELEGRAM_TOKEN = '7132952339:AAEKw5bcSKZl3y3AZrT03LsAR85iWp_yyRo'
WEBHOOK_URL = 'https://books-mu-ten.vercel.app/telegram'  # Замените на URL вашего приложения на Vercel

# Инициализация Telegram Application
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Модель базы данных для хранения метаданных файлов
class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    url = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, nullable=True)  # если вы хотите связать файлы с пользователем

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

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        
        file_content = file.read()
        file_url = upload_to_vercel_blob(filename, file_content)

        # Сохранение метаданных в базу данных
        new_file = File(filename=filename, url=file_url)
        db.session.add(new_file)
        db.session.commit()

        logger.debug(f"File metadata saved to database: {filename}, {file_url}")

        return jsonify({"file_url": file_url}), 201

    return jsonify({"error": "File type not allowed"}), 400

@app.route('/text-to-speech', methods=['POST', 'GET'])
def text_to_speech():
    if request.method == 'GET':
        return jsonify({"status": "Text-to-Speech endpoint is ready"}), 200

    data = request.json
    text = data.get('text')
    filename = secure_filename(data.get('filename') + '.mp3')
    model = data.get('model', 'en-US-GuyNeural')

    if not text or not filename:
        return jsonify({"error": "Please provide both text and filename"}), 400

    # Проверка, существует ли файл с таким именем
    existing_file = File.query.filter_by(filename=filename).first()
    if existing_file:
        logger.debug(f"File already exists: {filename}")
        return jsonify({"file_url": existing_file.url}), 200

    try:
        audio_content = generate_audio(text, model)
        file_url = upload_to_vercel_blob(filename, audio_content)

        # Сохранение метаданных в базу данных
        new_file = File(filename=filename, url=file_url)
        db.session.add(new_file)
        db.session.commit()

        logger.debug(f"File metadata saved to database: {filename}, {file_url}")

        return jsonify({"file_url": file_url}), 201

    except Exception as e:
        logger.error(f"Error in text-to-speech processing: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/get-file-url/<filename>', methods=['GET'])
def get_file_url(filename):
    file = File.query.filter_by(filename=filename).first()
    if file:
        return jsonify({"file_url": file.url}), 200
    else:
        return jsonify({"error": "File not found"}), 404

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

# Функции для Telegram Bot
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.debug("Received /start command")
    await update.message.reply_html(text="Привет! Я бот для преобразования текста в аудио. Выберите одну из двух книг: /book1 или /book2.")

async def book1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.debug("Received /book1 command")
    text = "Habits are the compound interest of self-improvement. Your net worth is a lagging measure of your financial habits. Your weight is a lagging measure of your eating habits. Your knowledge is a lagging measure of your learning habits. You get what you repeat."
    response = requests.post("http://127.0.0.1:5000/text-to-speech", json={"text": text, "filename": "book1_speech", "model": "en-US-GuyNeural"})
    file_url = response.json().get('file_url')
    await update.message.reply_text(f"Вот ваш аудиофайл: {file_url}")

async def book2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.debug("Received /book2 command")
    text = "If you want better results, forget about setting goals. Focus on your system instead. You do not rise to the level of your goals. You fall to the level of your systems. Your goal is your desired outcome. Your system is the collection of daily habits that will get you there."
    response = requests.post("http://127.0.0.1:5000/text-to-speech", json={"text": text, "filename": "book2_speech", "model": "en-US-GuyNeural"})
    file_url = response.json().get('file_url')
    await update.message.reply_text(f"Вот ваш аудиофайл: {file_url}")

@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    logger.debug("Received data from Telegram webhook")
    data = request.get_json(force=True)
    application.update_queue.put(Update.de_json(data, application.bot))
    return "ok", 200

def main():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("book1", book1))
    application.add_handler(CommandHandler("book2", book2))

    application.run_webhook(
        listen="0.0.0.0",
        port=5000,
        url_path='telegram',
        webhook_url=f"{WEBHOOK_URL}"
    )

if __name__ == '__main__':
    with app.app_context():
        db.create_all()  # Создание таблиц в базе данных

    main()
    app.run(host='0.0.0.0', port=5000)
