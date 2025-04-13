# Telegram Бот на Vercel

Это Telegram бот, работающий на платформе Vercel с использованием serverless функций.

## Особенности

- Telegram API для обработки сообщений через webhook
- Anthropic API для генерации контента
- Vercel Blob Storage для хранения аудиофайлов
- PostgreSQL база данных для хранения данных
- Redis для кэширования

## Развертывание на Vercel

### Подготовка

1. Создайте бота в Telegram через BotFather и получите токен
2. Зарегистрируйтесь на Vercel и создайте новый проект
3. Настройте переменные окружения в проекте Vercel:
   - `TELEGRAM_BOT_TOKEN`: токен вашего бота 
   - `ADMIN_CHAT_ID`: ID вашего чата в Telegram для получения уведомлений об ошибках
   - `ANTHROPIC_API_KEY`: API ключ от Anthropic
   - `BLOB_READ_WRITE_TOKEN`: токен для Vercel Blob Storage
   - `DATABASE_URL`: URL подключения к PostgreSQL
   - `REDIS`: URL подключения к Redis

### Деплой

1. Клонируйте этот репозиторий
2. Выполните команду `vercel` в корне проекта
3. Следуйте инструкциям CLI Vercel для завершения деплоя
4. После деплоя, бот автоматически настроит webhook на Vercel URL

### Установка webhook вручную

Если webhook не установился автоматически, вы можете установить его вручную, посетив URL:

```
https://<your-vercel-app-url>/set_webhook
```

## Локальная разработка

1. Создайте файл `.env` на основе `.env.example`
2. Установите зависимости: `pip install -r requirements.txt`
3. Запустите приложение: `python app.py`
4. Для тестирования webhook локально, используйте инструмент типа ngrok

## Структура проекта

- `app.py`: основной файл приложения
- `vercel.json`: конфигурация для Vercel
- `requirements.txt`: зависимости Python
- `.env`: переменные окружения (не включены в репозиторий)
- `system_message.txt`: системное сообщение для API Anthropic 