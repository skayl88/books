from flask import Flask, request, jsonify
import edge_tts
import os
import asyncio
from werkzeug.utils import secure_filename
import logging
import requests

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)
app.config['DEBUG'] = True

# Настройка Vercel Blob Storage
BLOB_READ_WRITE_TOKEN = os.getenv('BLOB_READ_WRITE_TOKEN')
BLOB_STORAGE_URL = "https://books-blob.vercel.app"

# Настройки приложения
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp3'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_to_blob_storage(filename, file_content):
    headers = {
        "Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}",
        "Content-Type": "application/octet-stream"
    }
    response = requests.post(f"{BLOB_STORAGE_URL}/{filename}", headers=headers, data=file_content)
    if response.status_code == 200:
        return response.json()["url"]
    else:
        logger.error(f"Failed to upload file to blob storage: {response.text}")
        raise Exception("Failed to upload file to blob storage")

@app.route('/', methods=['GET'])
def home():
    logger.info("Home route accessed")
    print("Home route accessed")
    return jsonify({"status": "Server is running"}), 200

@app.route('/upload', methods=['POST'])
def upload_file():
    logger.info("Upload route accessed")
    print("Upload route accessed")
    if 'file' not in request.files:
        logger.warning("No file part in the request")
        print("No file part in the request")
        return jsonify({"error": "No file part"}), 400

    file = request.files['file']

    if file.filename == '':
        logger.warning("No selected file")
        print("No selected file")
        return jsonify({"error": "No selected file"}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        logger.info(f"File allowed: {filename}")
        print(f"File allowed: {filename}")
        
        file_content = file.read()
        file_url = upload_to_blob_storage(filename, file_content)

        logger.info(f"File uploaded to Vercel Blob Storage: {filename}")
        print(f"File uploaded to Vercel Blob Storage: {filename}")

        return jsonify({"file_url": file_url}), 201

    logger.warning("File type not allowed")
    print("File type not allowed")
    return jsonify({"error": "File type not allowed"}), 400

@app.route('/text-to-speech', methods=['POST', 'GET'])
def text_to_speech():
    if request.method == 'GET':
        logger.info("Text-to-Speech GET route accessed")
        print("Text-to-Speech GET route accessed")
        return jsonify({"status": "Text-to-Speech endpoint is ready"}), 200

    logger.info("Text-to-Speech POST route accessed")
    print("Text-to-Speech POST route accessed")
    data = request.json
    text = data.get('text')
    filename = secure_filename(data.get('filename') + '.mp3')
    model = data.get('model', 'en-US-GuyNeural')

    if not text or not filename:
        logger.warning("Missing text or filename in the request")
        print("Missing text or filename in the request")
        return jsonify({"error": "Please provide both text and filename"}), 400

    try:
        audio_path = generate_audio(text, filename, model)
        logger.info(f"Audio generated: {audio_path}")
        print(f"Audio generated: {audio_path}")

        with open(audio_path, "rb") as audio_file:
            file_content = audio_file.read()
            file_url = upload_to_blob_storage(filename, file_content)

        logger.info(f"Audio file uploaded to Vercel Blob Storage: {filename}")
        print(f"Audio file uploaded to Vercel Blob Storage: {filename}")

        os.remove(audio_path)
        logger.info(f"Local audio file removed: {audio_path}")
        print(f"Local audio file removed: {audio_path}")

        return jsonify({"file_url": file_url}), 201

    except Exception as e:
        logger.error(f"Error during text-to-speech processing: {e}")
        print(f"Error during text-to-speech processing: {e}")
        return jsonify({"error": str(e)}), 500

def generate_audio(text, filename, model):
    logger.info(f"Generating audio for text: {text} with model: {model}")
    print(f"Generating audio for text: {text} with model: {model}")
    async def run():
        communicate = edge_tts.Communicate(text, model)
        await communicate.save(filename)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())
    loop.close()

    logger.info(f"Audio file saved as: {filename}")
    print(f"Audio file saved as: {filename}")
    return filename

if __name__ == '__main__':
    logger.info("Starting Flask server")
    print("Starting Flask server")
    app.run(host='0.0.0.0', port=5000)
