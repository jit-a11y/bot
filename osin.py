import os
import time
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart

from google import genai
from google.genai import types as genai_types

# ==========================================
# КОНФИГУРАЦИЯ И НАСТРОЙКА ЛОГИРОВАНИЯ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEYS_RAW = os.getenv("GOOGLE_API_KEYS")

if not TELEGRAM_BOT_TOKEN:
    logging.critical("🚨 Ошибка: Переменная окружения 'TELEGRAM_BOT_TOKEN' не установлена!")
if not GOOGLE_API_KEYS_RAW:
    logging.critical("🚨 Ошибка: Переменная окружения 'GOOGLE_API_KEYS' не установлена!")

MODEL_OSINT_FLASH = "gemini-2.5-flash"
MODEL_GEO_PRO = "gemini-2.5-flash"

# ==========================================
# РЕЖИМЫ РАБОТЫ ПОЛЬЗОВАТЕЛЕЙ
# ==========================================
user_modes = {}

# ==========================================
# ИНТЕЛЛЕКТУАЛЬНЫЙ СУПЕР-РОТАТОР КЛЮЧЕЙ
# ==========================================
class AdvancedSmartRotator:
    def __init__(self, raw_keys_string: str):
        if not raw_keys_string:
            self.pool = {}
            return
        keys = [key.strip() for key in raw_keys_string.split(",") if key.strip()]
        self.pool = {
            key: {
                "errors": 0,
                "blocked_until": 0.0,
                "success_count": 0,
                "weight": 100
            } for key in keys
        }
        logging.info(f"Загружен пул из {len(self.pool)} API-ключей из Environment Variables.")

    def get_best_key(self) -> str:
        now = time.time()
        if not self.pool:
            raise ValueError("Пул API-ключей пуст. Проверьте переменную GOOGLE_API_KEYS в Railway.")
        active_pool = {k: v for k, v in self.pool.items() if v["blocked_until"] < now}
        if not active_pool:
            logging.critical("🚨 ВСЕ АККАУНТЫ ЗАБЛОКИРОВАНЫ ЛИМИТАМИ! Вынужденный выбор наименее пострадавшего.")
            sorted_by_ban = sorted(self.pool.items(), key=lambda x: x[1]["blocked_until"])
            return sorted_by_ban[0][0]
        sorted_keys = sorted(
            active_pool.items(),
            key=lambda x: (x[1]["errors"], -x[1]["weight"], -x[1]["success_count"])
        )
        best_key = sorted_keys[0][0]
        logging.info(f"🎯 Выбран оптимальный ключ: {best_key[:12]}... [Ошибок: {self.pool[best_key]['errors']}, Успехов: {self.pool[best_key]['success_count']}]")
        return best_key

    def report_success(self, key: str):
        self.pool[key]["success_count"] += 1
        if self.pool[key]["errors"] > 0:
            self.pool[key]["errors"] -= 1
        self.pool[key]["weight"] = min(100, self.pool[key]["weight"] + 5)

    def report_failure(self, key: str, is_quota_issue: bool = True):
        self.pool[key]["errors"] += 1
        self.pool[key]["weight"] = max(0, self.pool[key]["weight"] - 25)
        if is_quota_issue:
            self.pool[key]["blocked_until"] = time.time() + 75
            logging.warning(f"🛑 Ключ {key[:12]}... заблокирован лимитами (429). Бан на 75 сек.")
        else:
            self.pool[key]["blocked_until"] = time.time() + 15
            logging.warning(f"⚠️ Ключ {key[:12]}... выдал ошибку среды. Кулдаун 15 сек.")

rotator = AdvancedSmartRotator(GOOGLE_API_KEYS_RAW or "")
dp = Dispatcher()

# ==========================================
# ИНСТРУКЦИИ ДЛЯ СИСТЕМЫ (SYSTEM PROMPTS)
# ==========================================
OSINT_SYSTEM_INSTRUCTION = """
Ты — старший аналитик автономного OSINT-терминала. Твоя цель — собрать, сопоставить и структурировать информацию из открытых источников по входящему запросу.
Запрос может содержать: Telegram ID, юзернейм, номер телефона, email, ФИО, IP-адрес, домен, хэши или никнеймы.
Используй инструменты поиска для проверки глобальной сети, утечек, упоминаний на форумах, в репозиториях и соцсетях.
Если предоставленных данных критически мало для развернутого отчета, или поисковая выдача пуста, ты ОБЯЗАН включить в ответ маркер: [TRIGGER_CASCADE_PRO].

**Форматирование ответа (ОБЯЗАТЕЛЬНО):**
- Весь основной текст должен быть выделен **жирным шрифтом**.
- Для второстепенных замечаний, дат и источников используй _курсив_.
- Для критических выводов, предупреждений и ключевых зацепок используй цитирование (> текст).
- Приводи прямые ссылки на источники.
- Оформляй блоки markdown-списками.
- Ответ должен быть максимально лаконичным, не более 500 токенов.
"""

PHONE_SYSTEM_INSTRUCTION = """
Ты — специалист по телефонной разведке. Проведи детальный анализ номера телефона: оператор, регион, упоминания в базах данных, мессенджерах, объявлениях, соцсетях.
Проверь связанные аккаунты, утечки и историю активности.

**Форматирование ответа (ОБЯЗАТЕЛЬНО):**
- Весь основной текст должен быть выделен **жирным шрифтом**.
- Для второстепенных замечаний используй _курсив_.
- Для критических выводов используй цитирование (> текст).
- Максимальная конкретика и лаконичность. Не более 500 токенов.
"""

EMAIL_SYSTEM_INSTRUCTION = """
Ты — аналитик по цифровой разведке. Проведи расследование по email-адресу или домену: регистрационные данные, связанные аккаунты, утечки, MX-записи, поддомены, история веб-архивов.
Выяви связи с другими цифровыми активами.

**Форматирование ответа (ОБЯЗАТЕЛЬНО):**
- Весь основной текст должен быть выделен **жирным шрифтом**.
- Для второстепенных замечаний используй _курсив_.
- Для критических выводов используй цитирование (> текст).
- Не более 500 токенов.
"""

NICKNAME_SYSTEM_INSTRUCTION = """
Ты — аналитик по профилированию цифровой личности. Проведи глубокий поиск по никнейму: перекрестные ссылки на платформах, история смены ников, связанные аккаунты, аватарки, биографии, активность на форумах и в играх.
Составь карту цифрового присутствия.

**Форматирование ответа (ОБЯЗАТЕЛЬНО):**
- Весь основной текст должен быть выделен **жирным шрифтом**.
- Для второстепенных замечаний используй _курсив_.
- Для критических выводов используй цитирование (> текст).
- Не более 500 токенов.
"""

GEOOSINT_SYSTEM_INSTRUCTION = """
Ты — эксперт военной разведки в области GeoOSINT и фотограмметрии. Твоя задача — деконструировать изображение до пикселей для точной или приблизительной локализации объекта.
Проведи глубокий анализ по следующим паттернам:
1. АНАЛИЗ ТЕНЕЙ И ИНСОЛЯЦИИ: Оцени направление, геометрию и длину теней. Рассчитай примерное положение солнца, азимут, сторону света и время суток.
2. АРХИТЕКТУРНЫЙ КОД: Определи тип застройки, форму оконных рам, материал кровли, цоколи.
3. ИНФРАСТРУКТУРНЫЕ МАРКЕРЫ: Форма дорожных знаков, разметка, светофоры, фонари, гидранты, люки, ЛЭП, госномера.
4. СИМВОЛЫ И ТЕКСТ: Сканируй любые надписи, вывески, граффити, ценники, языковые диалекты, логотипы.
5. БИОМЫ И КЛИМАТ: Изучи растительность, рельеф, почву, погодные условия.

**Форматирование ответа (ОБЯЗАТЕЛЬНО):**
- Весь основной текст должен быть выделен **жирным шрифтом**.
- Для второстепенных замечаний используй _курсив_.
- Для критических выводов используй цитирование (> текст).
- В конце предложи готовые поисковые дорки для карт.
- Не более 500 токенов.
"""

CASCADE_SYSTEM_INSTRUCTION = """
Ты — элитный аналитик закрытых расследований. Собери все упоминания, связи, старые ники, возможные утечки информации.
Очисти ответ от системных маркеров. Весь текст **жирным**, второстепенное _курсивом_, ключевые выводы в цитировании (>).
Не более 500 токенов.
"""

# ==========================================
# REPLY КЛАВИАТУРА (постоянно под полем ввода)
# ==========================================
def get_reply_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [
                types.KeyboardButton(text="🌐 Универсальный"),
                types.KeyboardButton(text="📱 Телефон")
            ],
            [
                types.KeyboardButton(text="📧 Email"),
                types.KeyboardButton(text="🧑‍💻 Ник")
            ],
            [
                types.KeyboardButton(text="📸 GeoOSINT"),
                types.KeyboardButton(text="🔄 Перезапуск")
            ],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите режим или отправьте цель..."
    )

# ==========================================
# ОБРАБОТЧИКИ КОМАНД И КНОПОК
# ==========================================
@dp.message(CommandStart())
async def process_start_command(message: types.Message):
    welcome_text = (
        "🤖 **Терминал Агрегации Данных OSINT/GeoOSINT**\n\n"
        "Система функционирует в штатном режиме. Нагрузка распределяется между независимыми каналами сбора данных с каскадным переключением модулей:\n"
        "• Первичный пробив текстовых данных: **Модуль быстрого сканирования** (с выходом в Web в реальном времени).\n"
        "• Интеллектуальный допробив и анализ изображений: **Модуль глубокой аналитики**.\n\n"
        "_Выберите режим работы кнопкой ниже, затем отправьте цель для анализа._"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_reply_keyboard())

@dp.message(F.text == "🔄 Перезапуск")
async def handle_restart(message: types.Message):
    user_modes.pop(message.from_user.id, None)
    await process_start_command(message)

@dp.message(F.text.in_(["🌐 Универсальный", "📱 Телефон", "📧 Email", "🧑‍💻 Ник", "📸 GeoOSINT"]))
async def set_mode(message: types.Message):
    mode_map = {
        "🌐 Универсальный": "universal",
        "📱 Телефон": "phone",
        "📧 Email": "email",
        "🧑‍💻 Ник": "nickname",
        "📸 GeoOSINT": "geo"
    }
    mode = mode_map[message.text]
    user_modes[message.from_user.id] = mode
    await message.answer(
        f"**Режим активирован:** {message.text}\n\n"
        f"_Отправьте цель для анализа._",
        parse_mode="Markdown"
    )

# ==========================================
# ЦЕНТРАЛЬНАЯ ЛОГИКА: ТЕКСТОВЫЙ OSINT (КАСКАД)
# ==========================================
@dp.message(F.text & ~F.text.startswith('/') & ~F.text.in_(["🌐 Универсальный", "📱 Телефон", "📧 Email", "🧑‍💻 Ник", "📸 GeoOSINT", "🔄 Перезапуск"]))
async def handle_osint_request(message: types.Message):
    user_id = message.from_user.id
    mode = user_modes.get(user_id, "universal")
    
    # Выбор системного промпта и статуса в зависимости от режима
    if mode == "phone":
        system_prompt = PHONE_SYSTEM_INSTRUCTION
        status_text = "📱 Инициализация телефонного сканирования..."
    elif mode == "email":
        system_prompt = EMAIL_SYSTEM_INSTRUCTION
        status_text = "📧 Сбор данных по email и доменной инфраструктуре..."
    elif mode == "nickname":
        system_prompt = NICKNAME_SYSTEM_INSTRUCTION
        status_text = "🧑‍💻 Поиск цифрового следа по никнейму..."
    else:
        system_prompt = OSINT_SYSTEM_INSTRUCTION
        status_text = "🔍 Инициализация сканирования открытых источников..."
    
    status_msg = await message.answer(status_text, parse_mode="Markdown")
    user_query = message.text
    final_response = ""
    used_key_flash = None

    # Шаг 1: Первичный поиск
    for attempt in range(4):
        try:
            used_key_flash = rotator.get_best_key()
            client = genai.Client(api_key=used_key_flash)
            response = client.models.generate_content(
                model=MODEL_OSINT_FLASH,
                contents=f"Выполни OSINT-пробив по целям: {user_query}",
                config=genai_types.GenerateContentConfig(
                    tools=[{"google_search": {}}],
                    system_instruction=system_prompt,
                    max_output_tokens=500
                )
            )
            final_response = response.text
            rotator.report_success(used_key_flash)
            break
        except Exception as e:
            if used_key_flash:
                is_429 = "429" in str(e) or "quota" in str(e).lower()
                rotator.report_failure(used_key_flash, is_quota_issue=is_429)

    # Шаг 2: Каскад на глубокий анализ
    if "[TRIGGER_CASCADE_PRO]" in final_response or not final_response:
        await status_msg.edit_text("🎯 Переключение на глубокий аналитический режим...")
        
        for attempt in range(4):
            try:
                used_key_pro = rotator.get_best_key()
                client = genai.Client(api_key=used_key_pro)
                pro_response = client.models.generate_content(
                    model=MODEL_GEO_PRO,
                    contents=f"Проведи тотальный, углубленный OSINT-анализ (предыдущая итерация не дала полных результатов): {user_query}",
                    config=genai_types.GenerateContentConfig(
                        tools=[{"google_search": {}}],
                        system_instruction=CASCADE_SYSTEM_INSTRUCTION,
                        max_output_tokens=500
                    )
                )
                final_response = pro_response.text
                rotator.report_success(used_key_pro)
                break
            except Exception as e:
                if used_key_pro:
                    is_429 = "429" in str(e) or "quota" in str(e).lower()
                    rotator.report_failure(used_key_pro, is_quota_issue=is_429)

    # Форматированный вывод
    if final_response:
        final_response = final_response.replace("[TRIGGER_CASCADE_PRO]", "").strip()
        if len(final_response) > 4096:
            for chunk in range(0, len(final_response), 4096):
                await message.answer(final_response[chunk:chunk+4096], parse_mode="Markdown")
        else:
            await status_msg.edit_text(final_response, parse_mode="Markdown")
    else:
        await status_msg.edit_text("❌ Критическая ошибка: Не удалось получить ответ от аналитического кластера. Проверьте лимиты аккаунтов в панели управления.")

# ==========================================
# ЦЕНТРАЛЬНАЯ ЛОГИКА: ВИЗУАЛЬНЫЙ GEOOSINT
# ==========================================
@dp.message(F.photo)
async def handle_geo_photo(message: types.Message, bot: Bot):
    status_msg = await message.answer("📸 Получено изображение. Запускаю визуальную деконструкцию объекта...", parse_mode="Markdown")
    
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    photo_buffer = await bot.download_file(file_info.file_path)
    img_bytes = photo_buffer.read()

    active_key = None
    for attempt in range(5):
        try:
            active_key = rotator.get_best_key()
            client = genai.Client(api_key=active_key)
            response = client.models.generate_content(
                model=MODEL_GEO_PRO,
                contents=[
                    genai_types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    "Выполни комплексное GeoOSINT исследование данного снимка."
                ],
                config=genai_types.GenerateContentConfig(
                    system_instruction=GEOOSINT_SYSTEM_INSTRUCTION,
                    max_output_tokens=500
                )
            )
            rotator.report_success(active_key)
            
            answer_text = response.text
            if len(answer_text) > 4096:
                for chunk in range(0, len(answer_text), 4096):
                    await message.answer(answer_text[chunk:chunk+4096], parse_mode="Markdown")
            else:
                await status_msg.edit_text(answer_text, parse_mode="Markdown")
            return
        except Exception as e:
            if active_key:
                is_429 = "429" in str(e) or "quota" in str(e).lower()
                rotator.report_failure(active_key, is_quota_issue=is_429)

    await status_msg.edit_text("❌ Не удалось произвести визуальный анализ. Все доступные API-ключи исчерпали лимиты запросов.")

# ==========================================
# ТОЧКА ВХОДА ДЛЯ RAILWAY
# ==========================================
async def start_application():
    if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEYS_RAW:
        logging.critical("🛑 Запуск невозможен. Проверьте переменные окружения в настройках Railway!")
        return
        
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info("🚀 OSINT/GeoOSINT Бот успешно запущен через переменные окружения.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(start_application())
        
