#!/bin/bash
# deploy.sh

echo "🚀 Начало деплоя Telegram бота..."

# Проверка наличия Docker
if ! command -v docker &> /dev/null; then
    echo "❌ Docker не установлен!"
    exit 1
fi

# Проверка переменных окружения
if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN не установлен!"
    exit 1
fi

# Создание .env файла
if [ ! -f .env ]; then
    echo "📝 Создание .env файла из шаблона..."
    cp .env.example .env
    echo "⚠️ Отредактируйте .env файл перед продолжением!"
    exit 1
fi

# Сборка образа
echo "🔨 Сборка Docker образа..."
docker build -t heatmap-bot:latest .

# Остановка и удаление старого контейнера
echo "🔄 Остановка старого контейнера..."
docker stop heatmap-bot 2>/dev/null || true
docker rm heatmap-bot 2>/dev/null || true

# Запуск нового контейнера
echo "🚀 Запуск нового контейнера..."
docker run -d \
  --name heatmap-bot \
  --restart unless-stopped \
  --env-file .env \
  -v heatmap-bot-data:/data \
  -v $(pwd)/logs:/app/logs \
  heatmap-bot:latest

echo "✅ Деплой завершен!"
echo ""
echo "📊 Просмотр логов: docker logs -f heatmap-bot"
echo "🛑 Остановка бота: docker stop heatmap-bot"
echo "▶️ Запуск бота: docker start heatmap-bot"
echo "🔧 Вход в контейнер: docker exec -it heatmap-bot bash"