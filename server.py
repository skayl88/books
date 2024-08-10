import os
from flask import Flask

app = Flask(__name__)

# Получаем ANTHROPIC_API_KEY из переменных окружения
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

@app.route('/')
def home():
    return f"Anthropic API Key is: {anthropic_api_key}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
