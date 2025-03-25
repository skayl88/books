import redis

KV_URL = ""

def test_redis_connection():
    try:
        # Инициализируем Redis с парсером hiredis
        redis_client = redis.StrictRedis.from_url(KV_URL, socket_connect_timeout=1000, socket_timeout=3000)
        
        # Пробуем установить и получить ключ для проверки
        redis_client.set('test_key', 'test_value')
        value = redis_client.get('test_key')
        print(f"Connected to Redis via URL successfully, test_key: {value.decode()}")
    except Exception as e:
        print(f"Error connecting to Redis via URL: {e}")

# Выполняем тест
test_redis_connection()
