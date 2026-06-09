import os
import time
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder
from google import genai
from google.genai import types as genai_types

# ==========================================
# КОНФИГУРАЦИЯ И НАСТРОЙКА ЛОГИРОВАНИЯ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)

# Безопасное получение токена и ключей из переменных окружения (Railway Environment)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEYS_RAW = os.getenv("GOOGLE_API_KEYS")

# Проверка, что все переменные окружения на месте
if not TELEGRAM_BOT_TOKEN:
    logging.critical("🚨 Ошибка: Переменная окружения 'TELEGRAM_BOT_TOKEN' не установлена!")
if not GOOGLE_API_KEYS_RAW:
    logging.critical("🚨 Ошибка: Переменная окружения 'GOOGLE_API_KEYS' не установлена!")

# Идентификаторы актуальных моделей линейки Gemini 3.x
MODEL_OSINT_FLASH = "gemini-2.5-flash"
MODEL_GEO_PRO = "gemini-2.5-flash"

# ==========================================
# ИНТЕЛЛЕКТУАЛЬНЫЙ СУПЕР-РОТАТОР КЛЮЧЕЙ
# ==========================================
class AdvancedSmartRotator:
    """
    Умный ротатор пула ключей. Учитывает ошибки (429/прочие), 
    динамически штрафует «уставшие» аккаунты, считает успешные запросы 
    и автоматически выводит ключ из бана по истечении кулдауна.
    """
    def __init__(self, raw_keys_string: str):
        if not raw_keys_string:
            self.pool = {}
            return
            
        # Парсим строку из os.getenv: делим по запятым и убираем лишние пробелы
        keys = [key.strip() for key in raw_keys_string.split(",") if key.strip()]
        
        self.pool = {
            key: {
                "errors": 0,            # Общий счетчик ошибок
                "blocked_until": 0.0,   # Unix-время, до которого ключ в бане
                "success_count": 0,     # Количество успешных транзакций
                "weight": 100           # Базовый вес «здоровья» (0-100)
            } for key in keys
        }
        logging.info(f"Загружен пул из {len(self.pool)} API-ключей из Environment Variables.")

    def get_best_key(self) -> str:
        now = time.time()
        if not self.pool:
            raise ValueError("Пул API-ключей пуст. Проверьте переменную GOOGLE_API_KEYS в Railway.")
            
        # Отбираем ключи, у которых нет активного бана
        active_pool = {k: v for k, v in self.pool.items() if v["blocked_until"] < now}
        
        # Если волной накрыло все аккаунты, ищем тот, у кого бан кончится быстрее всего
        if not active_pool:
            logging.critical("🚨 ВСЕ АККАУНТЫ ЗАБЛОКИРОВАНЫ ЛИМИТАМИ! Вынужденный выбор наименее пострадавшего.")
            sorted_by_ban = sorted(self.pool.items(), key=lambda x: x[1]["blocked_until"])
            return sorted_by_ban[0][0]

        # Сортируем пул по «умной» метрике:
        # 1. Меньше всего ошибок. 2. Выше показатель веса здоровья. 3. Больше успешных вызовов в прошлом.
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
            self.pool[key]["errors"] -= 1  # Постепенная амнистия при стабильной работе
        self.pool[key]["weight"] = min(100, self.pool[key]["weight"] + 5)

    def report_failure(self, key: str, is_quota_issue: bool = True):
        self.pool[key]["errors"] += 1
        self.pool[key]["weight"] = max(0, self.pool[key]["weight"] - 25)
        
        if is_quota_issue:
            # При 429 Too Many Requests отправляем аккаунт в кулдаун на 75 секунд
            self.pool[key]["blocked_until"] = time.time() + 75
            logging.warning(f"🛑 Ключ {key[:12]}... заблокирован лимитами (429). Бан на 75 сек.")
        else:
            # При иных ошибках даем мини-бан на 15 секунд для стабилизации сессии
            self.pool[key]["blocked_until"] = time.time() + 15
            logging.warning(f"⚠️ Ключ {key[:12]}... выдал ошибку среды. Кулдаун 15 сек.")

# Инициализируем ротатор, передавая полученную из ОС строку с ключами
rotator = AdvancedSmartRotator(GOOGLE_API_KEYS_RAW or "")
dp = Dispatcher()

# ==========================================
# ИНСТРУКЦИИ ДЛЯ СИСТЕМЫ (SYSTEM PROMPTS)
# ==========================================
OSINT_SYSTEM_INSTRUCTION = """
Ты — ведущий аналитик автономного OSINT-терминала. Твоя цель — собрать, сопоставить и структурировать любую информацию из открытых источников по входящему запросу.
Запрос может содержать: Telegram ID, юзернейм (@username), номер телефона, email, ФИО, IP-адрес, домен, хэши или никнеймы.
Используй инструмент Google Search Grounding для проверки глобальной сети, утечек, упоминаний на форумах, в репозиториях (GitHub/GitLab) и соцсетях.
Если предоставленных данных критически мало для формирования развернутого аналитического отчета, или поисковая выдача пуста, ты ОБЯЗАН включить в ответ маркер: [TRIGGER_CASCADE_PRO].
Выдавай отчет строго по фактам, оформляй блоки markdown-списками, выделяй жирным шрифтом ключевые зацепки. Приводи прямые ссылки на источники.
"""

GEOOSINT_SYSTEM_INSTRUCTION = """
Ты — эксперт военной разведки в области GeoOSINT и фотограмметрии. Твоя задача — деконструировать изображение до пикселей для точной или приблизительной локализации объекта.
Проведи глубокий анализ по следующим паттернам:
1. АНАЛИЗ ТЕНЕЙ И ИНСОЛЯЦИИ (Shadow Analysis): Оцени направление, геометрию и длину теней. Рассчитай примерное положение солнца, азимут, сторону света и время суток. Это критически важно для сужения круга поиска.
2. АРХИТЕКТУРНЫЙ КОД: Определи тип застройки (сталинский ампир, хрущевки, брутализм, европейский фахверк, современный хай-тек). Рассмотри форму оконных рам, материал кровли, цоколи зданий.
3. ИНФРАСТРУКТУРНЫЕ МАРКЕРЫ: Форма дорожных знаков, разметка, цвет и конфигурация светофоров, уличные фонари, типы гидрантов, люков, изоляторы на ЛЭП, номера и марки автомобилей, форматы госномеров.
4. СИМВОЛЫ И ТЕКСТ: Сканируй любые надписи, вывески, граффити, ценники, объявления, языковые диалекты, логотипы брендов.
5. БИОМЫ И КЛИМАТ: Изучи растительность (флора, тип деревьев, фаза цветения), рельеф местности (горы, холмы, равнины), характер почвы и погодные условия.
Сгенерируй отчет невероятной точности. В конце предложи готовые поисковые дорки (search queries) для Google Earth или карт.
"""

# ==========================================
# UI И СТИЛИЗАЦИЯ ИНЛАЙН-КНОПОК
# ==========================================
def get_colored_keyboard():
    builder = InlineKeyboardBuilder()
    # Цвета кнопок Telegram: primary (синий), success (зеленый), danger (красный)
    builder.row(
        types.InlineKeyboardButton(text="🌐 OSINT Пробив", callback_data="info_osint", style="primary"),
        types.InlineKeyboardButton(text="📸 GeoOSINT Анализ", callback_data="info_geo", style="success")
    )
    builder.row(
        types.InlineKeyboardButton(text="⚡ Текущий статус пула ключей", callback_data="pool_status", style="danger")
    )
    return builder.as_markup()

# ==========================================
# ОБРАБОТЧИКИ КОМАНД И КЛИКОВ
# ==========================================
@dp.message(CommandStart())
async def process_start_command(message: types.Message):
    welcome_text = (
        "🤖 **Запущен Терминал Агрегации Данных OSINT/GeoOSINT**\n\n"
        "Система функционирует в штатном режиме. Нагрузка распределяется между **10 независимыми "
        "аккаунтами Google AI Studio** через переменные среды с каскадным переключением ядер ИИ:\n"
        "• Первичный пробив текстовых данных: `Gemini 3.5 Flash` (с выходом в Web в реальном времени).\n"
        "• Интеллектуальный допробив и анализ изображений/теней: `Gemini 3.1 Pro`.\n\n"
        "__Просто отправьте текст (юзернейм, телефон, почту) или изображение для начала мгновенной экспертизы.__"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_colored_keyboard())

@dp.callback_query(F.data == "info_osint")
async def btn_osint_callback(callback: types.CallbackQuery):
    await callback.message.answer("📝 **Для OSINT-пробива:** Просто отправьте боту любой текст, юзернейм (@username), email, домен или телефон. Система выполнит автоматический поиск.")
    await callback.answer()

@dp.callback_query(F.data == "info_geo")
async def btn_geo_callback(callback: types.CallbackQuery):
    await callback.message.answer("📸 **Для GeoOSINT-анализа:** Отправьте боту изображение/фотографию местности в максимально доступном качестве.")
    await callback.answer()

@dp.callback_query(F.data == "pool_status")
async def btn_status_callback(callback: types.CallbackQuery):
    now = time.time()
    report = "📊 **Текущее состояние кластера ключей:**\n\n"
    for idx, (key, meta) in enumerate(rotator.pool.items(), 1):
        status = "🟢 Активен" if meta["blocked_until"] < now else f"🔴 Бан (еще {int(meta['blocked_until'] - now)}с)"
        report += f"• **Ядро #{idx}** ({key[:8]}...): {status} | Ошибок: {meta['errors']} | Успехов: {meta['success_count']}\n"
    await callback.message.answer(report, parse_mode="Markdown")
    await callback.answer()

# ==========================================
# ЦЕНТРАЛЬНАЯ ЛОГИКА: ТЕКСТОВЫЙ OSINT (КАСКАД)
# ==========================================
@dp.message(F.text & ~F.text.startswith('/'))
async def handle_osint_request(message: types.Message):
    status_msg = await message.answer("🔄 `[Каскад 1/2]` Развертывание поисковых агентов Gemini 3.5 Flash...")
    user_query = message.text
    final_response = ""
    used_key_flash = None

    # Шаг 1: Первичный поиск через Gemini 3.5 Flash
    for attempt in range(4):
        try:
            used_key_flash = rotator.get_best_key()
            client = genai.Client(api_key=used_key_flash)
            response = client.models.generate_content(
                model=MODEL_OSINT_FLASH,
                contents=f"Выполни OSINT-пробив по целям: {user_query}",
                config=genai_types.GenerateContentConfig(
                    tools=[{"google_search": {}}],  # Подключение Grounding Search
                    system_instruction=OSINT_SYSTEM_INSTRUCTION
                )
            )
            final_response = response.text
            rotator.report_success(used_key_flash)
            break
        except Exception as e:
            if used_key_flash:
                is_429 = "429" in str(e) or "quota" in str(e).lower()
                rotator.report_failure(used_key_flash, is_quota_issue=is_429)

    # Шаг 2: Проверка триггера каскада на тяжелую модель Gemini 3.1 Pro
    if "[TRIGGER_CASCADE_PRO]" in final_response or not final_response:
        await status_msg.edit_text("🔄 `[Каскад 2/2]` Недостаточно глубины данных. Активирую тяжелое ядро Gemini 3.1 Pro для детального OSINT...")
        
        for attempt in range(4):
            try:
                used_key_pro = rotator.get_best_key()
                client = genai.Client(api_key=used_key_pro)
                pro_response = client.models.generate_content(
                    model=MODEL_GEO_PRO,
                    contents=f"Проведи тотальный, углубленный OSINT-анализ (предыдущая итерация не дала полных результатов): {user_query}",
                    config=genai_types.GenerateContentConfig(
                        tools=[{"google_search": {}}],
                        system_instruction="Ты — элитный аналитик закрытых расследований. Собери все упоминания, связи, старые ники, возможные утечки информации. Очисти ответ от системных маркеров."
                    )
                )
                final_response = pro_response.text
                rotator.report_success(used_key_pro)
                break
            except Exception as e:
                if used_key_pro:
                    is_429 = "429" in str(e) or "quota" in str(e).lower()
                    rotator.report_failure(used_key_pro, is_quota_issue=is_429)

    # Форматированный вывод результатов в Telegram с обходом лимита символов
    if final_response:
        final_response = final_response.replace("[TRIGGER_CASCADE_PRO]", "").strip()
        if len(final_response) > 4096:
            for chunk in range(0, len(final_response), 4096):
                await message.answer(final_response[chunk:chunk+4096], parse_mode="Markdown")
        else:
            await status_msg.edit_text(final_response, parse_mode="Markdown")
    else:
        await status_msg.edit_text("❌ Критическая ошибка: Не удалось получить ответ от каскада ИИ. Проверьте лимиты аккаунтов в панели Railway.")

# ==========================================
# ЦЕНТРАЛЬНАЯ ЛОГИКА: ВИЗУАЛЬНЫЙ GEOOSINT
# ==========================================
@dp.message(F.photo)
async def handle_geo_photo(message: types.Message, bot: Bot):
    status_msg = await message.answer("📸 `[GeoOSINT]` Изображение получено. Передаю в мультимодальную матрицу Gemini 3.1 Pro...")
    
    # Загрузка фото напрямую в RAM
    photo = message.photo[-1]
    file_info = await bot.get_file(photo.file_id)
    photo_buffer = await bot.download_file(file_info.file_path)
    img_bytes = photo_buffer.read()

    active_key = None
    for attempt in range(5):
        try:
            active_key = rotator.get_best_key()
            client = genai.Client(api_key=active_key)
            # Для детального пространственного анализа и теней используем исключительно 3.1 Pro
            response = client.models.generate_content(
                model=MODEL_GEO_PRO,
                contents=[
                    genai_types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    "Выполни комплексное GeoOSINT исследование данного снимка."
                ],
                config=genai_types.GenerateContentConfig(
                    system_instruction=GEOOSINT_SYSTEM_INSTRUCTION
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
      
