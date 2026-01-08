import telebot
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from io import BytesIO

API_TOKEN = '7330339638:AAGTMJmANqau4-7ucGW8wTa_rPMb_f5uVF8'

bot = telebot.TeleBot(API_TOKEN)

data = None

# Этапы ввода
TITLE, X_LABEL, Y_LABEL, SHOW_VALUES = range(4)
user_data = {}

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Привет! Загрузите файл Excel для создания тепловой карты.")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    global data
    if message.document.mime_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':
        file_id = message.document.file_id
        file_info = bot.get_file(file_id)
        file = bot.download_file(file_info.file_path)
        
        file_bytes = BytesIO(file)
        data = pd.read_excel(file_bytes)
        bot.send_message(message.chat.id, "Файл успешно загружен. Введите название карты:")
        user_data[message.chat.id] = {'state': TITLE}
    else:
        bot.send_message(message.chat.id, "Пожалуйста, загрузите файл Excel.")

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == TITLE)
def set_title(message):
    user_data[message.chat.id]['title'] = message.text
    bot.send_message(message.chat.id, "Введите подпись оси X:")
    user_data[message.chat.id]['state'] = X_LABEL

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == X_LABEL)
def set_xlabel(message):
    user_data[message.chat.id]['xlabel'] = message.text
    bot.send_message(message.chat.id, "Введите подпись оси Y:")
    user_data[message.chat.id]['state'] = Y_LABEL

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == Y_LABEL)
def set_ylabel(message):
    user_data[message.chat.id]['ylabel'] = message.text
    bot.send_message(message.chat.id, "Отображать числовые значения в ячейках? Ответьте 'Да' или 'Нет'.")
    user_data[message.chat.id]['state'] = SHOW_VALUES

@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get('state') == SHOW_VALUES)
def show_values_choice(message):
    show_values = message.text.strip().lower() == 'да'
    user_data[message.chat.id]['show_values'] = show_values
    bot.send_message(message.chat.id, "Создание тепловой карты...")
    
    create_heatmap(message.chat.id)
    
    # Очистка данных после выполнения
    user_data.pop(message.chat.id, None)

def create_heatmap(chat_id):
    global data

    title = user_data.get(chat_id, {}).get('title', 'Тепловая карта')
    xlabel = user_data.get(chat_id, {}).get('xlabel', '')
    ylabel = user_data.get(chat_id, {}).get('ylabel', '')
    show_values = user_data.get(chat_id, {}).get('show_values', False)

    data_values = data.iloc[:, 1:].values.astype(float)
    data_ylabels = data.iloc[:, 0].values.astype(str)
    data_xlabels = data.columns[1:].astype(str)

    fig, ax = plt.subplots()
    heatmap = ax.imshow(data_values, cmap='cool', interpolation='nearest')

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.xticks(np.arange(len(data_xlabels)), data_xlabels)
    plt.yticks(np.arange(len(data_ylabels)), data_ylabels)

    plt.colorbar(heatmap)

    if show_values:
        for i in range(len(data_ylabels)):
            for j in range(len(data_xlabels)):
                plt.text(j, i, f'{data_values[i, j]:.1f}', ha='center', va='center', color='black')

    plt.text(1, 1.05, "Создано в @hystodate_bot", fontsize=8, color='gray',
             ha='right', va='bottom', transform=ax.transAxes)

    buf = BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    bot.send_photo(chat_id=chat_id, photo=buf)
    plt.close(fig)

if __name__== "__main__":
    bot.polling(none_stop=True)