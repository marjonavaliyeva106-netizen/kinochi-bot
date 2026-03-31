import sys
try:
    import asyncio
    import logging
    import sqlite3
    import random
    from aiogram import Bot, Dispatcher, types, F
    from aiogram.filters import Command
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, BotCommand
    from aiogram.fsm.state import State, StatesGroup
    from aiogram.fsm.context import FSMContext
except ModuleNotFoundError as e:
    missing = getattr(e, "name", str(e))
    print(f"Error: Missing dependency '{missing}'.")
    print("Install required packages with:")
    print("    pip install -r requirements.txt")
    sys.exit(1)

# --- KONFIGURATSIYA ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_URL = "https://t.me/nexora_startup"

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- MA'LUMOTLAR BAZASI ---
conn = sqlite3.connect("kinochi.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS movies (code TEXT PRIMARY KEY, file_id TEXT, caption TEXT)")
conn.commit()

# --- HOLATLAR ---
class AdminStates(StatesGroup):
    waiting_for_movie = State()
    waiting_for_ad = State()

# --- MENU BUYRUQLARINI O'RNATISH ---
async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Botni qayta tushirish"),
        BotCommand(command="help", description="Yordam va qo'llanma"),
        BotCommand(command="admin", description="Admin panel (faqat admin uchun)")
    ]
    await bot.set_my_commands(commands)

# --- TUGMALAR ---
def check_sub_btn():
    kb = [[InlineKeyboardButton(text="Obuna bo'lish", url=CHANNEL_URL)],
          [InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")]]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_inline_menu():
    kb = [
        [InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="add_movie")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="stats"), 
         InlineKeyboardButton(text="📢 Reklama", callback_data="broadcast")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- OBUNA TEKSHIRISH ---
async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# --- COMMAND HANDLERLAR ---

@dp.message(Command("start"))
async def start_cmd(message: Message):
    cursor.execute("INSERT OR IGNORE INTO users VALUES (?)", (message.from_user.id,))
    conn.commit()
    
    if not await is_subscribed(message.from_user.id):
        await message.answer(f"Xush kelibsiz! Botdan foydalanish uchun kanalga a'zo bo'ling.", reply_markup=check_sub_btn())
        return

    await message.answer(f"Assalomu alaykum {message.from_user.full_name}!\nKino kodini kiriting:")

@dp.message(Command("help"))
async def help_cmd(message: Message):
    help_text = (
        "📖 **Botdan foydalanish qo'llanmasi:**\n\n"
        "1. Kanalga a'zo bo'ling.\n"
        "2. Kinoning maxsus kodini yuboring.\n"
        "3. Bot sizga kinoni yuboradi.\n\n"
        "Agarda xatolik bo'lsa @admin_user ga murojaat qiling."
    )
    await message.answer(help_text, parse_mode="Markdown")

@dp.message(Command("admin"))
async def admin_cmd(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Boshqaruv paneli:", reply_markup=admin_inline_menu())
    else:
        await message.answer("Bu buyruq faqat adminlar uchun!")

# --- CALLBACKLAR ---
@dp.callback_query(F.data == "check_sub")
async def check_subscription(call: types.CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.delete()
        await call.message.answer("Tabriklaymiz! Obuna tasdiqlandi. Kino kodini yuboring.")
    else:
        await call.answer("Siz hali kanalga a'zo emassiz!", show_alert=True)

@dp.callback_query(F.data == "stats")
async def show_stats(call: types.CallbackQuery):
    cursor.execute("SELECT COUNT(*) FROM users")
    u_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM movies")
    m_count = cursor.fetchone()[0]
    await call.message.answer(f"📊 **Statistika:**\n\n👤 Foydalanuvchilar: {u_count}\n🎬 Kinolar: {m_count}")

@dp.callback_query(F.data == "add_movie")
async def start_add_movie(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Kino faylini (video) yuboring:")
    await state.set_state(AdminStates.waiting_for_movie)

# --- ADMIN FUNKSIYALARI ---
@dp.message(AdminStates.waiting_for_movie)
async def process_movie(message: Message, state: FSMContext):
    if message.video or message.document:
        code = str(random.randint(100, 9999)) # Tasodifiy kod
        file_id = message.video.file_id if message.video else message.document.file_id
        caption = message.caption or ""
        
        cursor.execute("INSERT INTO movies VALUES (?, ?, ?)", (code, file_id, caption))
        conn.commit()
        
        await message.answer(f"✅ Kino saqlandi!\nKino kodi: `{code}`", parse_mode="Markdown")
        await state.clear()
    else:
        await message.answer("Iltimos, video yoki fayl yuboring!")

@dp.callback_query(F.data == "broadcast")
async def start_broadcast(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Reklama xabarini yuboring (Rasm, video yoki tekst):")
    await state.set_state(AdminStates.waiting_for_ad)

@dp.message(AdminStates.waiting_for_ad)
async def process_ad(message: Message, state: FSMContext):
    cursor.execute("SELECT id FROM users")
    users = cursor.fetchall()
    for user in users:
        try:
            await message.copy_to(chat_id=user[0])
            await asyncio.sleep(0.05)
        except: pass
    await message.answer("Reklama tarqatildi.")
    await state.clear()

# --- KINO QIDIRISH (ASOSIY QISM) ---
@dp.message()
async def find_movie(message: Message):
    # Majburiy obunani har safar tekshirish
    if not await is_subscribed(message.from_user.id):
        await message.answer("Botdan foydalanish uchun kanalga a'zo bo'ling!", reply_markup=check_sub_btn())
        return

    code = message.text
    cursor.execute("SELECT file_id, caption FROM movies WHERE code = ?", (code,))
    movie = cursor.fetchone()

    if movie:
        file_id, caption = movie
        await message.answer_video(video=file_id, caption=caption)
    else:
        # Agar foydalanuvchi shunchaki nimanidir yozsa va u kod bo'lmasa
        if code.isdigit():
            await message.answer("❌ Bu kod bilan kino topilmadi.")
        else:
            await message.answer("Kino kodini raqamlarda kiriting.")

# --- ISHGA TUSHIRISH ---
async def main():
    await set_bot_commands(bot) # Menu buyruqlarini o'rnatish
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot to'xtatildi")