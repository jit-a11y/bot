import asyncio
import io
import time
import sqlite3
import aiohttp
from collections import deque
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

# Токен твоего бота (получи в @BotFather)
BOT_TOKEN = "8987822827:AAHM6Fnaijb-UCLvuEu5q_MaVQD3nl5h0go"

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# --- МЕНЕДЖЕР РОТАЦИИ API КЛЮЧЕЙ ---
class APIKeyManager:
    def __init__(self, api_keys):
        # Структура: [ключ, время_последнего_использования, счетчик_за_минуту]
        self.keys = deque([[key, 0.0, 0] for key in api_keys])
        self.lock = asyncio.Lock()

    async def get_available_key(self):
        async with self.lock:
            now = time.time()
            for _ in range(len(self.keys)):
                key_info = self.keys[0]
                key, last_used, request_count = key_info
                
                # Если минута прошла, обнуляем лимиты ключа
                if now - last_used > 60:
                    key_info[2] = 0
                    key_info[1] = now
                
                # Лимит персонального ключа: 10 запросов в минуту
                if key_info[2] < 10:
                    key_info[2] += 1
                    key_info[1] = now
                    self.keys.rotate(-1)  # Сдвигаем в конец очереди
                    return key
                
                self.keys.rotate(-1)
            
            # Если все 30 ключей забиты, спим и пробуем снова
            wait_time = max(0.1, 60 - (now - self.keys[0][1]))
            await asyncio.sleep(wait_time)
            return await self.get_available_key()

# Твои 30 личных API-ключей
MY_API_KEYS = [
    "Hd_d274411a572772c06aca2c65fc481d7a", "hd_a909b072a94237956e320162f556cead",
    "hd_8be9375870b8c43e74db1ad9b89db4fd", "hd_f8669181852a63bcc26c227a1f40bdb9",
    "hd_4d2184485ba4e82d6a43575a150a69da", "hd_494007fe2d98ebaa67598927c4a1f8e1",
    "hd_dfdccd73100a38645d614d76fc4e1fe3", "hd_78ac5f9a110ceff9a233e44e8f1fa4da",
    "hd_60439a2e7e7f05c24d0c75fd5fc58bfc", "hd_a8eebdfd9d3deaee20500a92c1d08a19",
    "hd_fe4257ece96b59c15305ce4bb78fbf06", "hd_cc0754606b4a716b40c148893f13f0a8",
    "hd_85c932b53c9b715f99d8aafbcc300676", "hd_de84775290213bf1a762e0a5246bb98e",
    "hd_7240d6a9b8af2c9442cd5db4c8788983", "hd_188cbbb023c581b427c04f41318c48c9",
    "hd_4cc9778501be443a1dac90eec9ad3ab7", "hd_ba8cb989ba912b913a3bcd3e97b6f809",
    "hd_47e079dc06c95a79e8eb257c5099f5b5", "hd_25862b6fc1f69eb682d6ca82b5baf384",
    "hd_12eb033a25bbce180cd126ff2bfd1f0a", "hd_e88d0d9e2f46da71fc37b2323a001c06",
    "hd_c38bf7c289b56f3703e2e4bfc96dcdda", "hd_acb950102a8b2d7707f03e07b778cb26",
    "hd_eaad6ce5e66183b5f55764e3075f98cc", "hd_4c069c150944683c033e274efe85fcba",
    "hd_246178b0d8b7fefb1f8aaeec3b485fb1", "hd_17109946740b25fd19e33743ce7c2bbd",
    "hd_a6033489c19fb266f95cff637abfe790", "hd_f7b032c29dcd69ce9b94b7176ff40a55"
]
key_manager = APIKeyManager(MY_API_KEYS)


# --- БАЗА ДАННЫХ (SQLite) ---
def init_db():
    with sqlite3.connect("users.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, lang TEXT)")
init_db()

def get_lang(user_id):
    with sqlite3.connect("users.db") as conn:
        res = conn.execute("SELECT lang FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return res[0] if res else None

def set_lang(user_id, lang):
    with sqlite3.connect("users.db") as conn:
        conn.execute("INSERT INTO users VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET lang=excluded.lang", (user_id, lang))


# --- СОСТОЯНИЯ FSM ---
class DecryptStates(StatesGroup):
    waiting_for_url = State()
    waiting_for_happ = State()


# --- КЛАВИАТУРЫ С ЦВЕТНЫМИ КНОПКАМИ (API 9.4+) ---
lang_kb = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🇷🇺 Русский", callback_data="set_lang_ru", style="danger"),
        InlineKeyboardButton(text="🇬🇧 English", callback_data="set_lang_en", style="danger")
    ]
])

def get_main_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 URL подписка" if lang=="ru" else "🔗 URL Subscription", callback_data="go_url", style="success")],
        [InlineKeyboardButton(text="🪙 Шифр от Happ" if lang=="ru" else "🪙 Happ Cipher", callback_data="go_happ", style="success")]
    ])


# --- ТЕКСТЫ (СТРОГО ЖИРНЫЙ ШРИФТ) ---
TEXTS = {
    "ru": {
        "welcome": "<b>👋 Привет! Я Ghost Kink Decrypt, я умею расшифровывать URL подписки, а также «happ://crypt».\n\nВыбери из кнопок, что ты именно хочешь расшифровать.</b>",
        "send_url": "<b>📥 Отправь мне URL подписки для дешифрации:</b>",
        "send_happ": "<b>📥 Отправь мне шифр happ:// для дешифрации:</b>",
        "wait": "<b>⏳ Расшифровываю данные через пулы Happy Decoder, пожалуйста, подожди...</b>",
        "err": "<b>❌ Произошла ошибка при дешифрации. Проверь правильность ссылки.</b>",
        "ok": "<b>✅ Готово! Все конфигурации выгружены в файл.</b>"
    },
    "en": {
        "welcome": "<b>👋 Hello! I am Ghost Kink Decrypt, I can decrypt subscription URLs as well as \"happ://crypt\".\n\nChoose from the buttons what exactly you want to decrypt.</b>",
        "send_url": "<b>📥 Send me the subscription URL to decrypt:</b>",
        "send_happ": "<b>📥 Send me the happ:// cipher to decrypt:</b>",
        "wait": "<b>⏳ Decrypting data via Happy Decoder pools, please wait...</b>",
        "err": "<b>❌ Decryption error. Please check if your link is valid.</b>",
        "ok": "<b>✅ Done! All configurations have been exported to the file.</b>"
    }
}


# --- ХЕНДЛЕРЫ НАВИГАЦИИ ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    lang = get_lang(message.from_user.id)
    if lang:
        await message.answer(TEXTS[lang]["welcome"], reply_markup=get_main_kb(lang))
    else:
        await message.answer("<b>Выберите язык / Select language:</b>", reply_markup=lang_kb)

@dp.callback_query(F.data.startswith("set_lang_"))
async def save_language(callback: CallbackQuery):
    lang = callback.data.split("_")[2]
    set_lang(callback.from_user.id, lang)
    await callback.answer()
    await callback.message.delete()
    await callback.message.answer(TEXTS[lang]["welcome"], reply_markup=get_main_kb(lang))

@dp.callback_query(F.data == "go_url")
async def route_url(callback: CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id) or "ru"
    await callback.answer()
    await callback.message.answer(TEXTS[lang]["send_url"])
    await state.set_state(DecryptStates.waiting_for_url)

@dp.callback_query(F.data == "go_happ")
async def route_happ(callback: CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id) or "ru"
    await callback.answer()
    await callback.message.answer(TEXTS[lang]["send_happ"])
    await state.set_state(DecryptStates.waiting_for_happ)


# --- КЛИЕНТСКАЯ ЧАСТЬ ДЕШИФРАТОРА ---
async def fetch_decrypted(url, state_type):
    """Отправляет запросы на API happy-decoder.cc в зависимости от типа ссылки"""
    async with aiohttp.ClientSession() as session:
        # Сценарий 1: Пользователь кинул URL подписки (используем Proxy эндпоинт БЕЗ КЛЮЧЕЙ)
        if state_type == "url":
            proxy_url = f"https://happy-decoder.cc/p/?u={url}"
            try:
                async with session.get(proxy_url, timeout=15) as resp:
                    if resp.status == 200:
                        return await resp.text()
            except Exception:
                return None

        # Сценарий 2: Пользователь кинул happ://crypt (Используем POST + ротацию наших 30 ключей)
        elif state_type == "happ":
            api_key = await key_manager.get_available_key()
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {"url": url}
            try:
                async with session.post("https://happy-decoder.cc/api/v1/decrypt", json=payload, headers=headers, timeout=15) as resp:
                    if resp.status == 200:
                        json_data = await resp.json()
                        # API возвращает JSON с полем decryptedUrl, внутри которого лежит подписка, 
                        # запрашиваем её содержимое напрямую, чтобы выдать конфиги текстом
                        target_sub = json_data.get("decryptedUrl")
                        if target_sub:
                            async with session.get(f"https://happy-decoder.cc/p/?u={target_sub}") as sub_resp:
                                if sub_resp.status == 200:
                                    return await sub_resp.text()
            except Exception:
                return None
    return None


# --- ОБРАБОТКА ССЫЛОК И ВЫДАЧА TXT ---
@dp.message(DecryptStates.waiting_for_url)
@dp.message(DecryptStates.waiting_for_happ)
async def process_decryption(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id) or "ru"
    raw_state = await state.get_state()
    state_type = "url" if raw_state == DecryptStates.waiting_for_url.state else "happ"
    
    user_link = message.text.strip()
    status_msg = await message.answer(TEXTS[lang]["wait"])
    
    # Запускаем дешифрацию
    result_data = await fetch_decrypted(user_link, state_type)
    await status_msg.delete()
    
    if not result_data or "error" in result_data.lower():
        await message.answer(TEXTS[lang]["err"])
    else:
        # Запихиваем результат (vless, vmess и т.д.) в буферный .txt файл
        file_bytes = result_data.encode("utf-8")
        txt_file = BufferedInputFile(file_bytes, filename="Ghost_Kink_Configs.txt")
        
        await message.answer_document(document=txt_file, caption=TEXTS[lang]["ok"])
        
    # Возвращаем юзера в меню
    await message.answer(TEXTS[lang]["welcome"], reply_markup=get_main_kb(lang))
    await state.clear()


async def main():
    print("[+] Бот Ghost Kink Decrypt успешно запущен на пуле из 30 ключей!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

