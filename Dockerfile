# Используем Python 3.10 как базовый образ
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Экспортируем переменные среды для конфигурации
ENV PORT=5000

# Открываем порт для приложения
EXPOSE 5000

# Команда запуска приложения
CMD ["python", "app.py"]
