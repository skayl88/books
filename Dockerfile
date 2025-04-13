# Используем Python 3.10 как базовый образ
FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем зависимости для сборки Python-пакетов
RUN apt-get update && apt-get install -y \
  gcc \
  python3-dev \
  libpq-dev \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .

# Устанавливаем зависимости с опцией --no-build-isolation для предотвращения проблем сборки
RUN pip install --no-cache-dir --no-build-isolation -r requirements.txt

# Копируем весь проект
COPY . .

# Экспортируем переменные среды для конфигурации
ENV PORT=5000

# Открываем порт для приложения
EXPOSE 5000

# Команда запуска приложения
CMD ["python", "app.py"]
