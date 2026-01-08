# main.py

import telebot
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')  # Для работы без GUI
import matplotlib.pyplot as plt
from io import BytesIO
import sqlite3
import json
from datetime import datetime
import os
import logging
from typing import Optional, Dict, Any

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
API_TOKEN = '7330339638:AAGTMJmANqau4-7ucGW8wTa_rPMb_f5uVF8'
if not API_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не установлен!")
    raise ValueError("TELEGRAM_BOT_TOKEN не установлен!")

bot = telebot.TeleBot(API_TOKEN)

# Пути
DATA_DIR = '/data'
DB_PATH = os.path.join(DATA_DIR, 'bot_database.db')
LOG_PATH = os.path.join(DATA_DIR, 'bot.log')

# Создаем директорию для данных, если её нет
os.makedirs(DATA_DIR, exist_ok=True)

# Глобальные переменные
data = None
TITLE, X_LABEL, Y_LABEL, SHOW_VALUES = range(4)
user_data = {}

class DatabaseManager:
    """Менеджер для работы с базой данных"""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        """Создает соединение с базой данных"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_database(self):
        """Инициализация базы данных"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Таблица пользователей
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                language_code TEXT,
                first_seen TIMESTAMP,
                last_active TIMESTAMP,
                total_requests INTEGER DEFAULT 0,
                total_heatmaps INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Таблица сессий
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action_type TEXT,
                file_name TEXT,
                parameters TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success BOOLEAN,
                error_message TEXT,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            ''')
            
            # Таблица ежедневной статистики
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date DATE UNIQUE,
                active_users INTEGER DEFAULT 0,
                total_requests INTEGER DEFAULT 0,
                successful_heatmaps INTEGER DEFAULT 0,
                failed_requests INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # Индексы для оптимизации
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON sessions(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON sessions(created_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_date ON daily_stats(date)')
            
            conn.commit()
            conn.close()
            logger.info("База данных инициализирована")
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
            raise
    
    def update_user_info(self, message) -> None:
        """Обновление информации о пользователе"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            user = message.from_user
            now = datetime.now()
            
            cursor.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, username, first_name, last_name, language_code, first_seen, last_active, total_requests)
            VALUES (?, ?, ?, ?, ?, 
                    COALESCE((SELECT first_seen FROM users WHERE user_id = ?), ?),
                    ?, COALESCE((SELECT total_requests FROM users WHERE user_id = ?), 0) + 1)
            ''', (user.id, user.username, user.first_name, user.last_name, user.language_code,
                  user.id, now, now, user.id))
            
            conn.commit()
            conn.close()
            logger.info(f"Обновлена информация о пользователе {user.id}")
        except Exception as e:
            logger.error(f"Ошибка обновления пользователя: {e}")
    
    def log_session(self, user_id: int, action_type: str, file_name: Optional[str] = None,
                   parameters: Optional[Dict[str, Any]] = None, success: bool = True,
                   error_message: Optional[str] = None) -> None:
        """Логирование сессии"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            INSERT INTO sessions 
            (user_id, action_type, file_name, parameters, success, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, action_type, file_name, 
                  json.dumps(parameters, ensure_ascii=False) if parameters else None,
                  success, error_message))
            
            # Если это успешное создание тепловой карты, увеличиваем счетчик
            if action_type == 'heatmap_created' and success:
                cursor.execute('''
                UPDATE users SET total_heatmaps = total_heatmaps + 1 
                WHERE user_id = ?
                ''', (user_id,))
            
            # Обновляем ежедневную статистику
            today = datetime.now().date()
            cursor.execute('''
            INSERT OR REPLACE INTO daily_stats (date, active_users, total_requests, 
                                               successful_heatmaps, failed_requests)
            VALUES (?, 
                    (SELECT COUNT(DISTINCT user_id) FROM users WHERE DATE(last_active) = ?),
                    COALESCE((SELECT total_requests FROM daily_stats WHERE date = ?), 0) + 1,
                    COALESCE((SELECT successful_heatmaps FROM daily_stats WHERE date = ?), 0) + (1 if ? = 'heatmap_created' AND ? else 0),
                    COALESCE((SELECT failed_requests FROM daily_stats WHERE date = ?), 0) + (0 if ? else 1))
            ''', (today, today, today, today, action_type, success, today, success))
            
            conn.commit()
            conn.close()
            logger.info(f"Записана сессия для пользователя {user_id}: {action_type}")
        except Exception as e:
            logger.error(f"Ошибка записи сессии: {e}")
    
    def get_user_stats(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Получение статистики пользователя"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
            SELECT username, first_name, last_name, first_seen, last_active, 
                   total_requests, total_heatmaps
            FROM users 
            WHERE user_id = ?
            ''', (user_id,))
            
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
        except Exception as e:
            logger.error(f"Ошибка получения статистики пользователя: {e}")
            return None
    
    def get_admin_stats(self) -> Dict[str, Any]:
        """Получение административной статистики"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            stats = {}
            
            # Основная статистика
            cursor.execute('SELECT COUNT(*) FROM users')
            stats['total_users'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM sessions WHERE success = 1')
            stats['successful_requests'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM sessions WHERE success = 0')
            stats['failed_requests'] = cursor.fetchone()[0]
            
            # Статистика за сегодня
            today = datetime.now().date()
            cursor.execute('''
            SELECT active_users, total_requests, successful_heatmaps, failed_requests
            FROM daily_stats WHERE date = ?
            ''', (today,))
            
            today_stats = cursor.fetchone()
            if today_stats:
                stats['today'] = dict(today_stats)
            
            # Топ пользователей
            cursor.execute('''
            SELECT username, first_name, total_requests, total_heatmaps, last_active
            FROM users ORDER BY total_requests DESC LIMIT 10
            ''')
            
            stats['top_users'] = [dict(row) for row in cursor.fetchall()]
            
            conn.close()
            return stats
        except Exception as e:
            logger.error(f"Ошибка получения админ-статистики: {e}")
            return {}
    
    def cleanup_old_sessions(self, days: int = 30):
        """Очистка старых сессий"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            cursor.execute('DELETE FROM sessions WHERE date(created_at) < ?', (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"Удалено {deleted_count} старых сессий")
            return deleted_count
        except Exception as e:
            logger.error(f"Ошибка очистки сессий: {e}")
            return 0

# Инициализация менеджера базы данных
db_manager = DatabaseManager(DB_PATH)

# Обработчики команд
@bot.message_handler(commands=['start'])
def start(message):
    """Обработчик команды /start"""
    db_manager.update_user_info(message)
    db_manager.log_session(message.from_user.id, 'start')
    
    welcome_text = """
👋 Привет! Я бот для создания тепловых карт из Excel-файлов.

📊 *Как работать с ботом:*
1. Отправьте мне файл Excel (.xlsx)
2. Укажите параметры для тепловой карты:
   - Название карты
   - Подпись оси X
   - Подпись оси Y
   - Отображение значений в ячейках

📈 *Доступные команды:*
/start - Начать работу
/stats - Ваша статистика
/help - Помощь

⚠️ *Формат Excel-файла:*
- Первый столбец: метки строк
- Остальные столбцы: числовые данные
- Максимальный размер: 20x20
    """
    
    bot.send_message(message.chat.id, welcome_text, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_command(message):
    """Обработчик команды /help"""
    help_text = """
🆘 *Помощь по использованию бота*

1. Подготовьте Excel-файл:
   - Первый столбец: названия строк
   - Остальные столбцы: числовые данные
   - Формат: .xlsx

2. Отправьте файл боту

3. Ответьте на вопросы:
   - Название тепловой карты
   - Подпись оси X
   - Подпись оси Y
   - Отображать ли значения в ячейках

4. Получите готовую тепловую карту!

📏 *Ограничения:*
- Максимальный размер файла: 5MB
- Максимальные размеры данных: 20x20
- Поддерживаемые форматы: .xlsx

🔄 Если что-то пошло не так, просто начните заново с команды /start

📊 Для просмотра вашей статистики используйте /stats
    """
    
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')
    db_manager.log_session(message.from_user.id, 'help_requested')

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Показать статистику пользователя"""
    db_manager.update_user_info(message)
    
    stats = db_manager.get_user_stats(message.from_user.id)
    
    if stats:
        user = message.from_user
        username = f"@{user.username}" if user.username else user.first_name
        
        stats_text = f"""
📊 *Ваша статистика*

👤 Пользователь: {username}
📅 Первый визит: {stats['first_seen']}
🕐 Последняя активность: {stats['last_active']}
📈 Всего запросов: {stats['total_requests']}
🖼 Создано карт: {stats['total_heatmaps']}

📈 *Активность:*
- Запросов в среднем: {stats['total_requests'] / max(1, (datetime.now() - datetime.strptime(stats['first_seen'], '%Y-%m-%d %H:%M:%S')).days):.1f} в день
        """
        
        bot.send_message(message.chat.id, stats_text, parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, "Вы еще не начали работу с ботом. Используйте /start")
    
    db_manager.log_session(message.from_user.id, 'stats_viewed')

@bot.message_handler(commands=['admin_stats'])
def admin_stats(message):
    """Показать общую статистику (для администраторов)"""
    # ID администраторов из переменной окружения
    admin_ids = os.getenv('ADMIN_IDS', '').split(',')
    admin_ids = [int(id.strip()) for id in admin_ids if id.strip().isdigit()]
    
    if message.from_user.id not in admin_ids:
        bot.send_message(message.chat.id, "⛔ У вас нет прав для просмотра этой статистики")
        return
    
    stats = db_manager.get_admin_stats()
    
    if not stats:
        bot.send_message(message.chat.id, "❌ Не удалось получить статистику")
        return
    
    today = datetime.now().date()
    
    stats_text = f"""
📊 *АДМИНИСТРАТИВНАЯ СТАТИСТИКА*

👥 Всего пользователей: {stats.get('total_users', 0)}
✅ Успешных запросов: {stats.get('successful_requests', 0)}
❌ Неудачных запросов: {stats.get('failed_requests', 0)}

*За сегодня ({today}):*
👥 Активных пользователей: {stats.get('today', {}).get('active_users', 0)}
📨 Всего запросов: {stats.get('today', {}).get('total_requests', 0)}
🖼 Создано карт: {stats.get('today', {}).get('successful_heatmaps', 0)}
⚠️ Ошибок: {stats.get('today', {}).get('failed_requests', 0)}
    """
    
    # Топ пользователей
    if stats.get('top_users'):
        stats_text += "\n\n🏆 *ТОП-10 ПОЛЬЗОВАТЕЛЕЙ:*\n"
        for i, user in enumerate(stats['top_users'][:10], 1):
            name = user.get('username') or user.get('first_name') or 'Аноним'
            requests = user.get('total_requests', 0)
            heatmaps = user.get('total_heatmaps', 0)
            stats_text += f"{i}. {name}: {requests} запросов ({heatmaps} карт)\n"
    
    bot.send_message(message.chat.id, stats_text, parse_mode='Markdown')
    db_manager.log_session(message.from_user.id, 'admin_stats_viewed')

@bot.message_handler(content_types=['document'])
def handle_document(message):
    """Обработка загруженных документов"""
    global data
    
    db_manager.update_user_info(message)
    
    # Проверка типа файла
    if (message.document.mime_type != 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' and
        not message.document.file_name.endswith('.xlsx')):
        bot.send_message(message.chat.id, "❌ Пожалуйста, загрузите файл Excel (.xlsx)")
        db_manager.log_session(
            message.from_user.id,
            'invalid_file_type',
            file_name=message.document.file_name,
            success=False,
            error_message=f"Invalid file type: {message.document.mime_type}"
        )
        return
    
    # Проверка размера файла (макс. 5MB)
    if message.document.file_size > 5 * 1024 * 1024:
        bot.send_message(message.chat.id, "❌ Файл слишком большой. Максимальный размер: 5MB")
        db_manager.log_session(
            message.from_user.id,
            'file_too_large',
            file_name=message.document.file_name,
            parameters={'file_size': message.document.file_size},
            success=False,
            error_message="File too large"
        )
        return
    
    try:
        bot.send_message(message.chat.id, "⏳ Загружаю файл...")
        
        file_id = message.document.file_id
        file_info = bot.get_file(file_id)
        file = bot.download_file(file_info.file_path)
        
        file_bytes = BytesIO(file)
        data = pd.read_excel(file_bytes)
        
        # Проверка размера данных
        if data.shape[0] > 20 or data.shape[1] > 21:  # 1 столбец для меток + 20 для данных
            bot.send_message(message.chat.id, "❌ Данные слишком большие. Максимальный размер: 20 строк × 20 столбцов")
            db_manager.log_session(
                message.from_user.id,
                'data_too_large',
                file_name=message.document.file_name,
                parameters={'shape': data.shape},
                success=False,
                error_message="Data too large"
            )
            return
        
        # Проверка, что данные числовые (кроме первого столбца)
        try:
            numeric_data = data.iloc[:, 1:].apply(pd.to_numeric, errors='coerce')
            if numeric_data.isnull().any().any():
                bot.send_message(message.chat.id, "⚠️ В файле есть нечисловые данные. Они будут заменены на 0")
                data.iloc[:, 1:] = numeric_data.fillna(0)
        except:
            data.iloc[:, 1:] = data.iloc[:, 1:].apply(pd.to_numeric, errors='coerce').fillna(0)
        
        db_manager.log_session(
            message.from_user.id,
            'file_uploaded',
            file_name=message.document.file_name,
            parameters={'shape': data.shape, 'file_size': message.document.file_size},
            success=True
        )
        
        bot.send_message(message.chat.id, "✅ Файл успешно загружен!\n\n📝 *Введите название тепловой карты:*", parse_mode='Markdown')
        user_data[message.chat.id] = {'state': TITLE, 'file_name': message.document.file_name}
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка обработки файла: {error_msg}")
        bot.send_message(message.chat.id, f"❌ Ошибка при обработке файла: {error_msg}")
        
        db_manager.log_session(
            message.from_user.id,
            'file_processing_error',
            file_name=message.document.file_name,
            success=False,
            error_message=error_msg
        )

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == TITLE)
def set_title(message):
    """Установка названия карты"""
    user_data[message.chat.id]['title'] = message.text
    user_data[message.chat.id]['state'] = X_LABEL
    bot.send_message(message.chat.id, "📝 *Введите подпись оси X:*", parse_mode='Markdown')

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == X_LABEL)
def set_xlabel(message):
    """Установка подписи оси X"""
    user_data[message.chat.id]['xlabel'] = message.text
    user_data[message.chat.id]['state'] = Y_LABEL
    bot.send_message(message.chat.id, "📝 *Введите подпись оси Y:*", parse_mode='Markdown')

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == Y_LABEL)
def set_ylabel(message):
    """Установка подписи оси Y"""
    user_data[message.chat.id]['ylabel'] = message.text
    user_data[message.chat.id]['state'] = SHOW_VALUES
    
    markup = telebot.types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add('Да', 'Нет')
    
    bot.send_message(message.chat.id, "🔢 *Отображать числовые значения в ячейках?*\n\nОтветьте 'Да' или 'Нет':", 
                     parse_mode='Markdown', reply_markup=markup)

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == SHOW_VALUES)
def show_values_choice(message):
    """Обработка выбора отображения значений"""
    show_values = message.text.strip().lower() == 'да'
    user_data[message.chat.id]['show_values'] = show_values
    
    # Удаляем клавиатуру
    markup = telebot.types.ReplyKeyboardRemove()
    bot.send_message(message.chat.id, "🔄 Создаю тепловую карту...", reply_markup=markup)
    
    create_heatmap(message.chat.id)

def create_heatmap(chat_id):
    """Создание и отправка тепловой карты"""
    global data
    
    try:
        user_info = user_data.get(chat_id, {})
        title = user_info.get('title', 'Тепловая карта')
        xlabel = user_info.get('xlabel', '')
        ylabel = user_info.get('ylabel', '')
        show_values = user_info.get('show_values', False)
        file_name = user_info.get('file_name', 'unknown')
        
        # Подготовка данных
        data_values = data.iloc[:, 1:].values.astype(float)
        data_ylabels = data.iloc[:, 0].astype(str).tolist()
        data_xlabels = data.columns[1:].astype(str).tolist()
        
        # Создание графика
        plt.figure(figsize=(10, 8))
        plt.imshow(data_values, cmap='coolwarm', interpolation='nearest', aspect='auto')
        plt.colorbar(label='Значение')
        
        plt.title(title, fontsize=14, pad=20)
        plt.xlabel(xlabel, fontsize=12)
        plt.ylabel(ylabel, fontsize=12)
        
        # Настройка осей
        plt.xticks(np.arange(len(data_xlabels)), data_xlabels, rotation=45, ha='right')
        plt.yticks(np.arange(len(data_ylabels)), data_ylabels)
        
        # Отображение значений
        if show_values:
            for i in range(len(data_ylabels)):
                for j in range(len(data_xlabels)):
                    value = data_values[i, j]
                    color = 'white' if value > np.mean(data_values) else 'black'
                    plt.text(j, i, f'{value:.2f}', ha='center', va='center', color=color, fontsize=8)
        
        plt.tight_layout()
        
        # Добавление водяного знака
        plt.figtext(0.99, 0.01, "Создано в @hystodate_bot", 
                   fontsize=8, color='gray', ha='right', va='bottom')
        
        # Сохранение в буфер
        buf = BytesIO()
        plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        plt.close()
        
        # Отправка изображения
        bot.send_photo(chat_id=chat_id, photo=buf, caption=f"✅ Готова тепловая карта: *{title}*", parse_mode='Markdown')
        
        # Логирование успеха
        db_manager.log_session(
            chat_id,
            'heatmap_created',
            file_name=file_name,
            parameters={
                'title': title,
                'xlabel': xlabel,
                'ylabel': ylabel,
                'show_values': show_values,
                'data_shape': data.shape,
                'file_name': file_name
            },
            success=True
        )
        
        # Очистка данных пользователя
        if chat_id in user_data:
            del user_data[chat_id]
        
        logger.info(f"Создана тепловая карта для пользователя {chat_id}")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Ошибка создания тепловой карты для {chat_id}: {error_msg}")
        
        bot.send_message(chat_id, f"❌ Ошибка при создании тепловой карты: {error_msg}")
        
        # Логирование ошибки
        db_manager.log_session(
            chat_id,
            'heatmap_error',
            file_name=user_data.get(chat_id, {}).get('file_name', 'unknown'),
            success=False,
            error_message=error_msg
        )
        
        # Очистка данных пользователя даже при ошибке
        if chat_id in user_data:
            del user_data[chat_id]

@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    """Обработка всех остальных сообщений"""
    if message.chat.id in user_data:
        # Если пользователь в процессе ввода, просим завершить его
        state = user_data[message.chat.id].get('state')
        if state == SHOW_VALUES:
            bot.send_message(message.chat.id, "Пожалуйста, ответьте 'Да' или 'Нет'")
        else:
            bot.send_message(message.chat.id, "Пожалуйста, завершите текущий ввод или начните заново с /start")
    else:
        # Если не в процессе, предлагаем начать
        bot.send_message(message.chat.id, "Используйте /start чтобы начать работу с ботом")
    
    db_manager.log_session(message.from_user.id, 'other_message')

def cleanup_old_data():
    """Очистка старых данных (запускается при старте)"""
    try:
        from datetime import timedelta
        deleted = db_manager.cleanup_old_sessions(days=30)
        logger.info(f"Очищено {deleted} старых записей")
    except Exception as e:
        logger.error(f"Ошибка очистки старых данных: {e}")

if __name__ == "__main__":
    logger.info("Запуск бота...")
    
    # Очистка старых данных при запуске
    cleanup_old_data()
    
    # Информация о боте
    bot_info = bot.get_me()
    logger.info(f"Бот запущен: @{bot_info.username} ({bot_info.first_name})")
    
    # Запуск бота
    try:
        bot.polling(none_stop=True, interval=1, timeout=30)
    except Exception as e:
        logger.error(f"Ошибка запуска бота: {e}")