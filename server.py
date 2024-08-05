from flask import Flask, request, jsonify, url_for, send_file
import edge_tts
import os
import asyncio
import tempfile

app = Flask(__name__)

@app.route('/', methods=['GET'])
def home():
    return jsonify({"status": "Server is running"}), 200

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

        return send_file(audio_path, as_attachment=True, download_name=f'{filename}.mp3')

    except Exception as e:
        return jsonify({"error": str(e)}), 500

async def generate_audio(text, filename, model):
    communicate = edge_tts.Communicate(text, model)
    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
        await communicate.save(tmp_file.name)
    return tmp_file.name

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
