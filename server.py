from flask import Flask, request, jsonify, url_for
import edge_tts
import os
import asyncio

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
        loop.run_until_complete(generate_audio(text, filename, model))
        loop.close()

        # Путь к аудио файлу
        audio_path = url_for('static', filename=f'{filename}.mp3', _external=True)
        return jsonify({"audio_url": audio_path})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

async def generate_audio(text, filename, model):
    communicate = edge_tts.Communicate(text, model)
    audio_path = os.path.join('static', f"{filename}.mp3")
    await communicate.save(audio_path)

if __name__ == '__main__':
    if not os.path.exists('static'):
        os.makedirs('static')
    app.run(host='0.0.0.0', port=5000)
