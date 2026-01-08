import telebot
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from io import BytesIO
import sqlite3
import json
from datetime import datetime, timedelta
import os
import logging
from typing import Optional, Dict, Any
import time

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

# Список администраторов
ADMIN_IDS = [5618005272, 1179013374]

bot = telebot.TeleBot(API_TOKEN)

# Пути
DATA_DIR = '/data'
DB_PATH = os.path.join(DATA_DIR, 'bot_database.db')
LOG_PATH = os.path.join(DATA_DIR, 'bot.log')

# Создаем директорию для данных, если её нет
os.makedirs(DATA_DIR, exist_ok=True)


# Глобальные переменные
TITLE, X_LABEL, Y_LABEL, SHOW_VALUES = range(4)
ADMIN_MENU = 4
ADMIN_BROADCAST = 5
ADMIN_BROADCAST_CONFIRM = 6
ADMIN_USER_MESSAGE = 7
ADMIN_USER_MESSAGE_CONFIRM = 8
user_data = {}
admin_states = {}  # Отдельный словарь для состояний админов

def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь администратором"""
    return user_id in ADMIN_IDS

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
            
            # Таблица ежедневной статистика
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
            
            # Сначала получаем текущие значения
            cursor.execute('SELECT * FROM daily_stats WHERE date = ?', (today,))
            existing = cursor.fetchone()
            
            if existing:
                # Обновляем существующую запись
                active_users = existing['active_users']
                total_requests = existing['total_requests'] + 1
                successful_heatmaps = existing['successful_heatmaps']
                failed_requests = existing['failed_requests']
                
                # Проверяем, нужно ли обновить active_users
                cursor.execute('''
                SELECT COUNT(DISTINCT user_id) as count 
                FROM users 
                WHERE DATE(last_active) = ?
                ''', (today,))
                active_users_count = cursor.fetchone()['count']
                
                # Обновляем значения в зависимости от типа действия
                if action_type == 'heatmap_created' and success:
                    successful_heatmaps += 1
                elif not success:
                    failed_requests += 1
                
                cursor.execute('''
                UPDATE daily_stats 
                SET active_users = ?,
                    total_requests = ?,
                    successful_heatmaps = ?,
                    failed_requests = ?
                WHERE date = ?
                ''', (active_users_count, total_requests, successful_heatmaps, failed_requests, today))
            else:
                # Создаем новую запись
                # Считаем активных пользователей за сегодня
                cursor.execute('''
                SELECT COUNT(DISTINCT user_id) as count 
                FROM users 
                WHERE DATE(last_active) = ?
                ''', (today,))
                active_users_count = cursor.fetchone()['count']
                
                successful_heatmaps = 1 if action_type == 'heatmap_created' and success else 0
                failed_requests = 0 if success else 1
                
                cursor.execute('''
                INSERT INTO daily_stats 
                (date, active_users, total_requests, successful_heatmaps, failed_requests)
                VALUES (?, ?, 1, ?, ?)
                ''', (today, active_users_count, successful_heatmaps, failed_requests))
            
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

*Основные команды:*
/start - Начать работу с ботом
/help - Показать эту справку
/stats - Ваша статистика
/cancel - Отменить текущую операцию
"""
    
    # Добавляем админ-команды для администраторов
    if is_admin(message.from_user.id):
        help_text += """
*Команды администратора:*
/admin - Панель администратора
/admin_stats - Общая статистика бота
"""
    
    help_text += """
*Как создать тепловую карту:*
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

🔄 Если что-то пошло не так, используйте /cancel
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
        
        # Форматируем даты
        first_seen = stats['first_seen']
        if isinstance(first_seen, str):
            first_seen_str = first_seen
        else:
            first_seen_str = first_seen.strftime('%Y-%m-%d %H:%M:%S')
        
        last_active = stats['last_active']
        if isinstance(last_active, str):
            last_active_str = last_active
        else:
            last_active_str = last_active.strftime('%Y-%m-%d %H:%M:%S')
        
        # Вычисляем среднее количество запросов в день
        try:
            if isinstance(first_seen, str):
                first_seen_date = datetime.strptime(first_seen_str, '%Y-%m-%d %H:%M:%S')
            else:
                first_seen_date = first_seen
                
            days_since = (datetime.now() - first_seen_date).days
            avg_requests = stats['total_requests'] / max(1, days_since)
        except:
            avg_requests = stats['total_requests']
        
        stats_text = f"""
📊 *Ваша статистика*

👤 Пользователь: {username}
📅 Первый визит: {first_seen_str}
🕐 Последняя активность: {last_active_str}
📈 Всего запросов: {stats['total_requests']}
🖼 Создано карт: {stats['total_heatmaps']}

📈 *Активность:*
- Запросов в среднем: {avg_requests:.1f} в день
        """
        
        bot.send_message(message.chat.id, stats_text, parse_mode='Markdown')
    else:
        bot.send_message(message.chat.id, "Вы еще не начали работу с ботом. Используйте /start")
    
    db_manager.log_session(message.from_user.id, 'stats_viewed')

@bot.message_handler(commands=['admin_stats'])
def admin_stats(message):
    """Показать общую статистику (для администраторов)"""
    # Используем единую функцию проверки
    if not is_admin(message.from_user.id):
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

@bot.message_handler(commands=['admin'])
def admin_menu(message):
    """Меню администратора"""
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ У вас нет прав доступа к админ-панели")
        return
    
    admin_states[message.chat.id] = {'state': ADMIN_MENU}
    
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('📢 Рассылка всем', '📨 Отправить пользователю')
    markup.add('📊 Статистика', '📋 Список пользователей')
    markup.add('🚫 Закрыть меню')
    
    bot.send_message(
        message.chat.id,
        "⚙️ *Панель администратора*\n\n"
        "Выберите действие:",
        parse_mode='Markdown',
        reply_markup=markup
    )
    
    db_manager.log_session(message.from_user.id, 'admin_menu_opened')

@bot.message_handler(func=lambda message: admin_states.get(message.chat.id, {}).get('state') == ADMIN_MENU)
def handle_admin_menu(message):
    """Обработка выбора в меню администратора"""
    if message.text == '📢 Рассылка всем':
        admin_states[message.chat.id] = {'state': ADMIN_BROADCAST}
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(
            message.chat.id,
            "📝 *Создание рассылки*\n\n"
            "Введите сообщение для рассылки всем пользователям.\n"
            "Вы можете использовать Markdown разметку.\n\n"
            "Для отмены отправьте /cancel",
            parse_mode='Markdown',
            reply_markup=markup
        )
    
    elif message.text == '📨 Отправить пользователю':
        admin_states[message.chat.id] = {'state': ADMIN_USER_MESSAGE}
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(
            message.chat.id,
            "👤 *Отправка сообщения пользователю*\n\n"
            "Введите ID пользователя и сообщение в формате:\n"
            "`ID_пользователя Текст сообщения`\n\n"
            "Пример:\n"
            "`123456789 Привет! Как дела?`\n\n"
            "Для отмены отправьте /cancel",
            parse_mode='Markdown',
            reply_markup=markup
        )
    
    elif message.text == '📊 Статистика':
        admin_stats(message)
    
    elif message.text == '📋 Список пользователей':
        show_users_list(message)
    
    elif message.text == '🚫 Закрыть меню':
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, "Меню закрыто", reply_markup=markup)
        if message.chat.id in admin_states:
            del admin_states[message.chat.id]
    
    else:
        bot.send_message(message.chat.id, "Пожалуйста, выберите действие из меню")

@bot.message_handler(func=lambda message: admin_states.get(message.chat.id, {}).get('state') == ADMIN_BROADCAST)
def handle_broadcast_message(message):
    """Обработка сообщения для рассылки"""
    if message.text == '/cancel':
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, "❌ Рассылка отменена", reply_markup=markup)
        del admin_states[message.chat.id]
        return
    
    admin_states[message.chat.id]['message'] = message.text
    admin_states[message.chat.id]['state'] = ADMIN_BROADCAST_CONFIRM
    
    # Подсчет пользователей
    user_count = count_total_users()
    
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('✅ Да, отправить', '❌ Нет, отменить')
    
    preview = message.text[:500] + "..." if len(message.text) > 500 else message.text
    
    bot.send_message(
        message.chat.id,
        f"📨 *Предпросмотр сообщения:*\n\n{preview}\n\n"
        f"📊 Будет отправлено: *{user_count}* пользователям\n\n"
        f"Подтверждаете отправку?",
        parse_mode='Markdown',
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: admin_states.get(message.chat.id, {}).get('state') == ADMIN_BROADCAST_CONFIRM)
def handle_broadcast_confirmation(message):
    """Подтверждение рассылки"""
    if message.text == '✅ Да, отправить':
        bot.send_message(message.chat.id, "🔄 Начинаю рассылку...")
        
        broadcast_message = admin_states[message.chat.id]['message']
        results = send_broadcast(message.chat.id, broadcast_message)
        
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(
            message.chat.id,
            f"✅ Рассылка завершена!\n\n"
            f"📊 Результаты:\n"
            f"• Успешно: {results['success']}\n"
            f"• Неудачно: {results['failed']}\n"
            f"• Всего: {results['total']}",
            reply_markup=markup
        )
        
        # Логирование
        db_manager.log_session(
            message.from_user.id,
            'broadcast_sent',
            parameters={
                'message_length': len(broadcast_message),
                'success': results['success'],
                'failed': results['failed'],
                'total': results['total']
            },
            success=True
        )
        
        del admin_states[message.chat.id]
    
    elif message.text == '❌ Нет, отменить':
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, "❌ Рассылка отменена", reply_markup=markup)
        del admin_states[message.chat.id]
    
    else:
        bot.send_message(message.chat.id, "Пожалуйста, выберите '✅ Да, отправить' или '❌ Нет, отменить'")

@bot.message_handler(func=lambda message: admin_states.get(message.chat.id, {}).get('state') == ADMIN_USER_MESSAGE)
def handle_user_message_input(message):
    """Обработка отправки сообщения конкретному пользователю"""
    if message.text == '/cancel':
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, "❌ Отправка отменена", reply_markup=markup)
        del admin_states[message.chat.id]
        return
    
    try:
        # Парсим ввод: первое число - ID, остальное - сообщение
        parts = message.text.split(' ', 1)
        if len(parts) < 2:
            raise ValueError("Неправильный формат")
        
        user_id = int(parts[0])
        user_message = parts[1]
        
        # Проверяем, существует ли пользователь
        user_info = db_manager.get_user_stats(user_id)
        if not user_info:
            bot.send_message(message.chat.id, f"❌ Пользователь с ID {user_id} не найден в базе")
            return
        
        # Сохраняем данные и меняем состояние на подтверждение
        admin_states[message.chat.id] = {
            'state': ADMIN_USER_MESSAGE_CONFIRM,
            'target_user_id': user_id,
            'user_message': user_message
        }
        
        markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add('✅ Отправить', '❌ Отменить')
        
        user_name = user_info.get('username', f"Пользователь {user_id}")
        preview = user_message[:300] + "..." if len(user_message) > 300 else user_message
        
        bot.send_message(
            message.chat.id,
            f"👤 *Отправка пользователю:* {user_name}\n"
            f"🆔 ID: {user_id}\n\n"
            f"📝 *Сообщение:*\n{preview}\n\n"
            f"Подтвердите отправку:",
            parse_mode='Markdown',
            reply_markup=markup
        )
        
    except ValueError as e:
        bot.send_message(
            message.chat.id,
            "❌ Неправильный формат. Используйте:\n"
            "`ID_пользователя Текст сообщения`\n\n"
            "Пример:\n"
            "`123456789 Привет! Проверяю работу бота.`",
            parse_mode='Markdown'
        )

@bot.message_handler(func=lambda message: admin_states.get(message.chat.id, {}).get('state') == ADMIN_USER_MESSAGE_CONFIRM)
def handle_user_message_confirmation(message):
    """Подтверждение отправки сообщения пользователю"""
    if message.text == '✅ Отправить':
        user_id = admin_states[message.chat.id]['target_user_id']
        user_message = admin_states[message.chat.id]['user_message']
        
        try:
            # Пытаемся отправить сообщение
            bot.send_message(user_id, user_message)
            
            markup = telebot.types.ReplyKeyboardRemove()
            bot.send_message(
                message.chat.id,
                f"✅ Сообщение успешно отправлено пользователю {user_id}",
                reply_markup=markup
            )
            
            # Логирование
            db_manager.log_session(
                message.from_user.id,
                'user_message_sent',
                parameters={'target_user_id': user_id, 'message_length': len(user_message)},
                success=True
            )
            
        except Exception as e:
            error_msg = str(e)
            bot.send_message(
                message.chat.id,
                f"❌ Не удалось отправить сообщение пользователю {user_id}\n"
                f"Ошибка: {error_msg}"
            )
            
            db_manager.log_session(
                message.from_user.id,
                'user_message_failed',
                parameters={'target_user_id': user_id, 'error': error_msg},
                success=False
            )
    
    elif message.text == '❌ Отменить':
        markup = telebot.types.ReplyKeyboardRemove()
        bot.send_message(message.chat.id, "❌ Отправка отменена", reply_markup=markup)
    
    # Удаляем состояние независимо от результата
    if message.chat.id in admin_states:
        del admin_states[message.chat.id]

def count_total_users() -> int:
    """Подсчет общего количества пользователей"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception as e:
        logger.error(f"Ошибка подсчета пользователей: {e}")
        return 0

def send_broadcast(admin_chat_id: int, message_text: str) -> dict:
    """Отправка рассылки всем пользователям"""
    results = {
        'success': 0,
        'failed': 0,
        'total': 0
    }
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users')
        users = cursor.fetchall()
        conn.close()
        
        results['total'] = len(users)
        
        bot.send_message(admin_chat_id, f"🔄 Отправка {len(users)} сообщений...")
        
        # Ограничиваем частоту отправки, чтобы не превысить лимиты Telegram
        for i, (user_id,) in enumerate(users):
            try:
                bot.send_message(user_id, message_text)
                results['success'] += 1
                
                # Отправляем статус каждые 10 сообщений
                if (i + 1) % 10 == 0:
                    bot.send_message(
                        admin_chat_id,
                        f"📊 Прогресс: {i + 1}/{len(users)} "
                        f"({((i + 1) / len(users) * 100):.1f}%)"
                    )
                
                # Пауза между сообщениями, чтобы не попасть в лимиты
                time.sleep(0.1)
                
            except Exception as e:
                results['failed'] += 1
                logger.error(f"Ошибка отправки пользователю {user_id}: {e}")
                
                # Если ошибка "Chat not found" или "bot was blocked", можно пропустить
                error_msg = str(e)
                if "chat not found" in error_msg.lower() or "bot was blocked" in error_msg.lower():
                    logger.info(f"Пользователь {user_id} заблокировал бота или чат не найден")
        
        return results
        
    except Exception as e:
        logger.error(f"Ошибка при рассылке: {e}")
        return results

def show_users_list(message):
    """Показать список пользователей"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Получаем последних 20 пользователей
        cursor.execute('''
            SELECT user_id, username, first_name, last_active, total_requests 
            FROM users 
            ORDER BY last_active DESC 
            LIMIT 20
        ''')
        
        users = cursor.fetchall()
        conn.close()
        
        if not users:
            bot.send_message(message.chat.id, "📭 В базе нет пользователей")
            return
        
        response = "📋 *Последние 20 пользователей:*\n\n"
        
        for i, user in enumerate(users, 1):
            user_id, username, first_name, last_active, total_requests = user
            
            # Форматируем имя
            if username:
                name = f"@{username}"
            elif first_name:
                name = first_name
            else:
                name = f"ID: {user_id}"
            
            # Форматируем дату
            if isinstance(last_active, str):
                last_seen = last_active[:16]
            else:
                last_seen = last_active.strftime('%Y-%m-%d %H:%M')
            
            response += f"{i}. {name}\n"
            response += f"   🆔: {user_id}\n"
            response += f"   📅: {last_seen}\n"
            response += f"   📊: {total_requests} запросов\n\n"
        
        # Добавляем общую статистику
        total_users = count_total_users()
        response += f"📈 Всего пользователей в базе: {total_users}"
        
        # Если сообщение слишком длинное, разбиваем на части
        if len(response) > 4000:
            parts = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for part in parts:
                bot.send_message(message.chat.id, part, parse_mode='Markdown')
        else:
            bot.send_message(message.chat.id, response, parse_mode='Markdown')
        
        db_manager.log_session(message.from_user.id, 'users_list_viewed')
        
    except Exception as e:
        logger.error(f"Ошибка получения списка пользователей: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка при получении списка: {str(e)}")

@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    """Отмена текущей операции"""
    if message.chat.id in admin_states:
        state = admin_states[message.chat.id].get('state')
        if state in [ADMIN_BROADCAST, ADMIN_BROADCAST_CONFIRM, ADMIN_USER_MESSAGE, ADMIN_USER_MESSAGE_CONFIRM]:
            markup = telebot.types.ReplyKeyboardRemove()
            bot.send_message(message.chat.id, "❌ Операция отменена", reply_markup=markup)
            del admin_states[message.chat.id]
    
    elif message.chat.id in user_data:
        state = user_data[message.chat.id].get('state')
        if state in [TITLE, X_LABEL, Y_LABEL, SHOW_VALUES]:
            markup = telebot.types.ReplyKeyboardRemove()
            bot.send_message(message.chat.id, "❌ Создание тепловой карты отменено", reply_markup=markup)
            del user_data[message.chat.id]
    
    else:
        bot.send_message(message.chat.id, "Нет активных операций для отмены")

@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    """Обработка всех остальных сообщений"""
    # Проверяем, не находится ли пользователь в админ-состоянии
    if message.chat.id in admin_states:
        state = admin_states[message.chat.id].get('state')
        
        if state == ADMIN_BROADCAST:
            # Ждем сообщение для рассылки
            handle_broadcast_message(message)
        elif state == ADMIN_BROADCAST_CONFIRM:
            # Ждем подтверждения рассылки
            handle_broadcast_confirmation(message)
        elif state == ADMIN_USER_MESSAGE:
            # Ждем ввод данных для отправки пользователю
            handle_user_message_input(message)
        elif state == ADMIN_USER_MESSAGE_CONFIRM:
            # Ждем подтверждения отправки пользователю
            handle_user_message_confirmation(message)
        else:
            bot.send_message(message.chat.id, "Пожалуйста, используйте меню администратора или /cancel для отмены")
    
    # Проверяем, не находится ли пользователь в обычном состоянии
    elif message.chat.id in user_data:
        state = user_data[message.chat.id].get('state')
        if state == SHOW_VALUES:
            bot.send_message(message.chat.id, "Пожалуйста, ответьте 'Да' или 'Нет'")
        else:
            bot.send_message(message.chat.id, "Пожалуйста, завершите текущий ввод или начните заново с /start")
    
    else:
        # Если не в процессе, предлагаем начать
        if is_admin(message.from_user.id):
            bot.send_message(message.chat.id, "Используйте /start для работы с ботом или /admin для админ-панели")
        else:
            bot.send_message(message.chat.id, "Используйте /start чтобы начать работу с ботом")
    
    db_manager.log_session(message.from_user.id, 'other_message')

def cleanup_old_data():
    """Очистка старых данных (запускается при старте)"""
    try:
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