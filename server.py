from flask import Flask, request, jsonify, send_file, url_for
import edge_tts
import os
import asyncio
import tempfile

app = Flask(__name__)

# Настройки
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
        with tempfile.NamedTemporaryFile(delete=False, dir='uploads') as tmp_file:
            file.save(tmp_file.name)
            return jsonify({"file_url": url_for('uploaded_file', filename=os.path.basename(tmp_file.name), _external=True)}), 201

    return jsonify({"error": "File type not allowed"}), 400

@app.route('/uploads/<filename>', methods=['GET'])
def uploaded_file(filename):
    return send_file(os.path.join('uploads', filename))

@app.route('/text-to-speech', methods=['POST', 'GET'])
def text_to_speech():
    if request.method == 'GET':
        return jsonify({"status": "Text-to-Speech endpoint is ready"}), 200

    data = request.json
    text = data.get('text')
    filename = data.get('filename')
    model = data.get('model', 'en-US-GuyNeural')  # используем модель по умолчанию, если не указана

    if not text or not filename:
        return jsonify({"error": "Please provide both text and filename"}), 400

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        audio_path = loop.run_until_complete(generate_audio(text, filename, model))
        loop.close()

        return jsonify({"file_url": url_for('uploaded_file', filename=os.path.basename(audio_path), _external=True)}), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500

async def generate_audio(text, filename, model):
    communicate = edge_tts.Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3', dir='uploads') as tmp_file:
        await communicate.save(tmp_file.name)
    return tmp_file.name

if __name__ == '__main__':
    if not os.path.exists('uploads'):
        os.makedirs('uploads')
    app.run(host='0.0.0.0', port=5000)
