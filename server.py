from flask import Flask, request, jsonify, url_for
import firebase_admin
from firebase_admin import credentials, storage
import edge_tts
import os
import asyncio
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Настройка Firebase
cred = credentials.Certificate('path/to/serviceAccountKey.json')  # Укажите путь к вашему файлу serviceAccountKey.json
firebase_admin.initialize_app(cred, {
    'storageBucket': 'books-cca25.appspot.com'  # Замените на ваше значение storageBucket
})
bucket = storage.bucket()

# Настройки приложения
ALLOWED_EXTENSIONS = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'mp3'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
        blob = bucket.blob(filename)
        blob.upload_from_file(file, content_type=file.content_type)
        blob.make_public()
        return jsonify({"file_url": blob.public_url}), 201

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
        audio_path = generate_audio(text, filename, model)

        blob = bucket.blob(filename)
        blob.upload_from_filename(audio_path)
        blob.make_public()
        os.remove(audio_path)

        return jsonify({"file_url": blob.public_url}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def generate_audio(text, filename, model):
    async def run():
        communicate = edge_tts.Communicate(text, model)
        await communicate.save(filename)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run())
    loop.close()

    return filename

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
