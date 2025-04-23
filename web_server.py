# web_server.py
import os
from flask import Flask, Response

# Создаем Flask приложение
app = Flask(__name__)

# Определяем порт из переменной окружения PORT,
# которую Render устанавливает автоматически для Web Services.
# Используем 10000 как порт по умолчанию для локального теста.
port = int(os.environ.get("PORT", 10000))

@app.route('/')
def health_check():
    """Отвечает на HTTP GET запросы Render для проверки здоровья."""
    # Возвращаем простой ответ 200 OK
    return Response("Bot helper is alive!", status=200)

if __name__ == '__main__':
    # Запускаем веб-сервер, слушая все интерфейсы (0.0.0.0)
    # debug=False для продакшена на Render
    app.run(host='0.0.0.0', port=port, debug=False)