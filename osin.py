import os
import re
import time
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.markdown import escape_md

from google import genai
from google.genai import types as genai_types

# ==========================================
# КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GOOGLE_API_KEYS_RAW = os.getenv("GOOGLE_API_KEYS")

if not TELEGRAM_BOT_TOKEN:
    logging.critical("🚨 TELEGRAM_BOT_TOKEN не установлен!")
if not GOOGLE_API_KEYS_RAW:
    logging.critical("🚨 GOOGLE_API_KEYS не установлен!")

MODEL_OSINT_FLASH = "gemini-2.5-flash"
MODEL_GEO_PRO = "gemini-2.5-flash"

user_modes = {}

# ==========================================
# РОТАТОР КЛЮЧЕЙ
# ==========================================
class AdvancedSmartRotator:
    def __init__(self, raw_keys_string: str):
        if not raw_keys_string:
            self.pool = {}
            return
        keys = [k.strip() for k in raw_keys_string.split(",") if k.strip()]
        self.pool = {
            key: {"errors": 0, "blocked_until": 0.0,
                  "success_count": 0, "weight": 100}
            for key in keys
        }
        logging.info(f"Загружен пул из {len(self.pool)} API-ключей.")

    def get_best_key(self) -> str:
        now = time.time()
        if not self.pool:
            raise ValueError("Пул API-ключей пуст. Проверьте GOOGLE_API_KEYS в Railway.")
        active_pool = {k: v for k, v in self.pool.items() if v["blocked_until"] < now}
        if not active_pool:
            sorted_by_ban = sorted(self.pool.items(), key=lambda x: x[1]["blocked_until"])
            return sorted_by_ban[0][0]
        sorted_keys = sorted(
            active_pool.items(),
            key=lambda x: (x[1]["errors"], -x[1]["weight"], -x[1]["success_count"])
        )
        best_key = sorted_keys[0][0]
        logging.info(f"🎯 Выбран ключ: {best_key[:12]}... [Ошибок: {self.pool[best_key]['errors']}, Успехов: {self.pool[best_key]['success_count']}]")
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
            logging.warning(f"🛑 {key[:12]}... 429-бан на 75 сек.")
        else:
            self.pool[key]["blocked_until"] = time.time() + 15
            logging.warning(f"⚠️ {key[:12]}... кулдаун 15 сек.")

rotator = AdvancedSmartRotator(GOOGLE_API_KEYS_RAW or "")
dp = Dispatcher()

# ==========================================
# СИСТЕМНЫЕ ПРОМПТЫ
# ==========================================
OSINT_SYSTEM_INSTRUCTION = """
Ты — старший аналитик автономного OSINT-терминала. Собери, сопоставь и структурируй информацию из открытых источников.
Запрос может содержать: Telegram ID, юзернейм, номер телефона, email, ФИО, IP-адрес, домен, хэши или никнеймы.
Если предоставленных данных критически мало для развернутого отчета, или поисковая выдача пуста, добавь в ответ маркер: [TRIGGER_CASCADE_PRO].

**Форматирование (MarkdownV2):**
- Основной текст — **жирным**.
- Второстепенное — _курсивом_.
- Критические выводы и предупреждения — цитатой (> текст).
- Прямые ссылки на источники.
- Списки markdown.
- Лаконично, не более 500 токенов.
"""

PHONE_SYSTEM_INSTRUCTION = """
Ты — специалист по телефонной разведке. Оператор, регион, упоминания в базах, мессенджерах, объявлениях, соцсетях.
Связанные аккаунты, утечки, история активности.

**Форматирование (MarkdownV2):**
- **жирный** основной текст, _курсив_ для деталей, > цитата для критических выводов.
- Не более 500 токенов.
"""

EMAIL_SYSTEM_INSTRUCTION = """
Ты — аналитик цифровой разведки. Регистрационные данные, связанные аккаунты, утечки, MX-записи, поддомены, веб-архивы.
Связи с другими цифровыми активами.

**Форматирование (MarkdownV2):** **жирный** основной, _курсив_ детали, > цитата выводы. До 500 токенов.
"""

NICKNAME_SYSTEM_INSTRUCTION = """
Ты — аналитик профилирования цифровой личности. Перекрёстные ссылки на платформах, история ников, связанные аккаунты, аватарки, биографии, активность.
Карта цифрового присутствия.

**Форматирование (MarkdownV2):** **жирный** основной, _курсив_ детали, > цитата выводы. До 500 токенов.
"""

GEOOSINT_SYSTEM_INSTRUCTION = """
Ты — эксперт военной разведки в GeoOSINT и фотограмметрии. Деконструируй изображение до пикселей для локализации.
1. ТЕНИ И ИНСОЛЯЦИЯ: направление, геометрия, длина теней, азимут, время суток.
2. АРХИТЕКТУРНЫЙ КОД: застройка, окна, кровля, цоколь.
3. ИНФРАСТРУКТУРНЫЕ МАРКЕРЫ: знаки, разметка, светофоры, фонари, гидранты, люки, ЛЭП, номера.
4. СИМВОЛЫ И ТЕКСТ: надписи, вывески, граффити, языковые диалекты, логотипы.
5. БИОМЫ И КЛИМАТ: растительность, рельеф, почва, погода.

**Форматирование (MarkdownV2):** **жирный** основной, _курсив_ детали, > цитата выводы. В конце — готовые поисковые дорки для карт. До 500 токенов.
"""

CASCADE_SYSTEM_INSTRUCTION = """
Ты — элитный аналитик закрытых расследований. Собери упоминания, связи, старые ники, утечки.
Очисти ответ от системных маркеров. **Жирный** текст, _курсив_ детали, > цитата выводы. До 500 токенов.
"""

# ==========================================
# INLINE-КЛАВИАТУРА С ЦВЕТАМИ
# ==========================================
def get_inline_keyboard() -> types.InlineKeyboardMarkup:
    """Inline-меню с разными цветами (style). Привязано к сообщению, не к полю ввода."""
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [
            types.InlineKeyboardButton(text="🌐 Универсальный",  callback_data="mode:universal", style="primary"),
            types.InlineKeyboardButton(text="📱 Телефон",        callback_data="mode:phone",     style="success"),
        ],
        [
            types.InlineKeyboardButton(text="📧 Email",          callback_data="mode:email",     style="info"),
            types.InlineKeyboardButton(text="🧑‍💻 Ник",          callback_data="mode:nickname",  style="warning"),
        ],
        [
            types.InlineKeyboardButton(text="📸 GeoOSINT",       callback_data="mode:geo",       style="success"),
            types.InlineKeyboardButton(text="🔄 Перезапуск",     callback_data="mode:restart",   style="danger"),
        ],
    ])

# ==========================================
# БЕЗОПАСНАЯ ОТПРАВКА: MarkdownV2 + escape + fallback
# ==========================================
def _strip_fences(text: str) -> str:
    """Снимает ```...``` обрамление, которое часто возвращает Gemini."""
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text.strip())
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()

def _trim_entities(text: str, limit: int = 4096) -> list[str]:
    """Режет длинный текст на чанки с запасом, чтобы не упереться в лимит Telegram."""
    return [text[i:i + limit] for i in range(0, len(text), limit)] if text else [""]

async def safe_edit_text(target_message: types.Message, text: str) -> None:
    """Edit с MarkdownV2 и экранированием. При ошибке парсинга — fallback на plain text."""
    if not text:
        return
    text = _strip_fences(text)
    try:
        await target_message.edit_text(escape_md(text), parse_mode="MarkdownV2")
    except TelegramBadRequest as e:
        logging.warning(f"MarkdownV2 edit упал ({e}), отправляю plain.")
        # Пытаемся без разметки
        try:
            await target_message.edit_text(text)
        except TelegramBadRequest:
            # Если не влезло — шлём кусками
            for chunk in _trim_entities(text):
                await target_message.chat.send_message(chunk)

async def safe_answer(target_message: types.Message, text: str) -> types.Message | None:
    """Answer (новое сообщение) с MarkdownV2 и fallback на plain text."""
    if not text:
        return None
    text = _strip_fences(text)
    try:
        return await target_message.answer(escape_md(text), parse_mode="MarkdownV2")
    except TelegramBadRequest as e:
        logging.warning(f"MarkdownV2 answer упал ({e}), отправляю plain.")
        try:
            return await target_message.answer(text)
        except TelegramBadRequest:
            last = None
            for chunk in _trim_entities(text):
                last = await target_message.answer(chunk)
            return last

# ==========================================
# ОБРАБОТЧИКИ КОМАНД И CALLBACK
# ==========================================
@dp.message(CommandStart())
async def process_start_command(message: types.Message):
    welcome_text = (
        "🤖 **Терминал Агрегации Данных OSINT/GeoOSINT**\n\n"
        "Система функционирует в штатном режиме:\n"
        "• Первичный пробив текстовых данных — **Модуль быстрого сканирования** (Web в реальном времени).\n"
        "• Допробив и анализ изображений — **Модуль глубокой аналитики**.\n\n"
        "_Выберите режим кнопкой ниже, затем отправьте цель для анализа._"
    )
    await safe_answer(message, welcome_text)
    # Отдельно отправляем клавиатуру, чтобы разметка не ломала её
    await message.answer(
        "👇 *Панель управления:*",
        parse_mode="MarkdownV2",
        reply_markup=get_inline_keyboard()
    )

# Callback-обработчик: одна функция на все кнопки
@dp.callback_query(F.data.startswith("mode:"))
async def on_mode_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    action = callback.data.split(":", 1)[1]

    if action == "restart":
        user_modes.pop(user_id, None)
        await callback.message.edit_text(
            "🔄 *Сессия сброшена\\.*\n\nВыберите режим заново:",
            parse_mode="MarkdownV2",
            reply_markup=get_inline_keyboard()
        )
        await callback.answer("Сессия сброшена")
        return

    title_map = {
        "universal": "🌐 Универсальный",
        "phone":     "📱 Телефон",
        "email":     "📧 Email",
        "nickname":  "🧑‍💻 Ник",
        "geo":       "📸 GeoOSINT",
    }
    user_modes[user_id] = action
    title = title_map.get(action, action)
    await callback.message.edit_text(
        f"✅ *Режим активирован:* {escape_md(title)}\n\n"
        f"_Отправьте цель для анализа\\._",
        parse_mode="MarkdownV2",
        reply_markup=get_inline_keyboard()
    )
    await callback.answer(f"Режим: {title}")

# ==========================================
# ЦЕНТРАЛЬНАЯ ЛОГИКА: ТЕКСТОВЫЙ OSINT (КАСКАД)
# ==========================================
@dp.message(F.text)
async def handle_osint_request(message: types.Message):
    user_id = message.from_user.id
    mode = user_modes.get(user_id, "universal")

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

    status_msg = await safe_answer(message, status_text)
    if not status_msg:
        status_msg = await message.answer(status_text)
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
        await safe_edit_text(status_msg, "🎯 Переключение на глубокий аналитический режим...")

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

    if final_response:
        final_response = final_response.replace("[TRIGGER_CASCADE_PRO]", "").strip()
        if len(final_response) > 4096:
            chunks = _trim_entities(final_response, 3500)  # запас под экранирование
            try:
                await status_msg.delete()
            except Exception:
                pass
            for chunk in chunks:
                await safe_answer(message, chunk)
        else:
            await safe_edit_text(status_msg, final_response)
    else:
        await safe_edit_text(
            status_msg,
            "❌ Критическая ошибка: Не удалось получить ответ от аналитического кластера. Проверьте лимиты аккаунтов."
        )

# ==========================================
# ЦЕНТРАЛЬНАЯ ЛОГИКА: ВИЗУАЛЬНЫЙ GEOOSINT
# ==========================================
@dp.message(F.photo)
async def handle_geo_photo(message: types.Message, bot: Bot):
    status_msg = await safe_answer(message, "📸 Получено изображение. Запускаю визуальную деконструкцию объекта...")

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

            answer_text = response.text or ""
            if len(answer_text) > 4096:
                chunks = _trim_entities(answer_text, 3500)
                try:
                    await status_msg.delete()
                except Exception:
                    pass
                for chunk in chunks:
                    await safe_answer(message, chunk)
            else:
                await safe_edit_text(status_msg, answer_text)
            return
        except Exception as e:
            if active_key:
                is_429 = "429" in str(e) or "quota" in str(e).lower()
                rotator.report_failure(active_key, is_quota_issue=is_429)

    await safe_edit_text(
        status_msg,
        "❌ Не удалось произвести визуальный анализ. Все доступные API-ключи исчерпали лимиты запросов."
    )

# ==========================================
# FALLBACK ДЛЯ НЕОПОЗНАННЫХ СООБЩЕНИЙ
# ==========================================
@dp.message()
async def fallback_handler(message: types.Message):
    await safe_answer(
        message,
        "⚠️ Не удалось распознать сообщение. Выберите режим ниже и повторите запрос."
    )
    await message.answer(
        "👇 *Панель управления:*",
        parse_mode="MarkdownV2",
        reply_markup=get_inline_keyboard()
    )

# ==========================================
# ТОЧКА ВХОДА ДЛЯ RAILWAY
# ==========================================
async def start_application():
    if not TELEGRAM_BOT_TOKEN or not GOOGLE_API_KEYS_RAW:
        logging.critical("🛑 Запуск невозможен. Проверьте переменные окружения в Railway!")
        return

    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    logging.info("🚀 OSINT/GeoOSINT Бот успешно запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(start_application())
