from flask import Flask, request, jsonify
import edge_tts
import os
import asyncio
from werkzeug.utils import secure_filename
import logging
import requests
import tempfile
import redis

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
app.config['DEBUG'] = True

# Настройка Upstash Redis
KV_URL = os.getenv('KV_URL')
KV_REST_API_URL = os.getenv('KV_REST_API_URL')
KV_REST_API_TOKEN = os.getenv('KV_REST_API_TOKEN')

if not KV_URL or not KV_REST_API_URL or not KV_REST_API_TOKEN:
    logger.error("KV_URL, KV_REST_API_URL, and KV_REST_API_TOKEN environment variables are not set")
    raise ValueError("KV_URL, KV_REST_API_URL, and KV_REST_API_TOKEN environment variables are not set")

redis_client = redis.from_url(KV_URL)

# Настройки приложения
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp3'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_upstash(filename, file_content):
    headers = {
        "Authorization": f"Bearer {KV_REST_API_TOKEN}",
        "Content-Type": "application/octet-stream"
    }
    response = requests.post(f"{KV_REST_API_URL}/set/{filename}", headers=headers, data=file_content)
    if response.status_code == 200:
        return f"{KV_REST_API_URL}/get/{filename}"
    else:
        logger.error(f"Failed to upload file to Upstash: {response.text}")
        raise Exception(f"Failed to upload file to Upstash: {response.status_code} - {response.text}")

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
        file_url = upload_to_upstash(filename, file_content)

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

    try:
        audio_content = generate_audio(text, model)
        file_url = upload_to_upstash(filename, audio_content)

        return jsonify({"file_url": file_url}), 201

    except Exception as e:
        logger.error(f"Error in text-to-speech processing: {e}")
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
    app.run(host='0.0.0.0', port=5000)
