import os
import asyncio
import logging
import random
import aiosqlite
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    Message, 
    BotCommand, 
    CallbackQuery
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError

# --- KONFIGURATSIYA ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Adminlarni ro'yxatga olish
admins_env = os.getenv("ADMINS", "")
ADMINS = [int(id.strip()) for id in admins_env.split(",") if id.strip().isdigit()]

# Kanallar ro'yxati
channels_env = os.getenv("CHANNELS", "@nexora_startup,@kinoch_ibot1")
CHANNELS = [id.strip() for id in channels_env.split(",") if id.strip()]

# Kanal URLlari
urls_env = os.getenv("CHANNEL_URLS", "https://t.me/nexora_startup,https://t.me/kinoch_ibot1")
CHANNEL_URLS = [url.strip() for url in urls_env.split(",") if url.strip()]

if not TOKEN:
    raise ValueError("BOT_TOKEN topilmadi!")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- MA'LUMOTLAR BAZASI ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "kinochi.db"))

async def init_db():
    db_dir = os.path.dirname(DB_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Migratsiya: Agar username ustuni bo'lmasa qo'shish
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
            await db.commit()
        except:
            pass

        await db.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY,
                file_id TEXT,
                caption TEXT,
                views INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

# --- HOLATLAR ---
class AdminStates(StatesGroup):
    waiting_for_movie = State()
    waiting_for_code = State()
    waiting_for_ad = State()

# --- TUGMALAR ---
def sub_kb():
    kb = []
    for i, url in enumerate(CHANNEL_URLS):
        kb.append([InlineKeyboardButton(text=f"📢 {i+1}-kanalga obuna bo'lish", url=url)])
    kb.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_kb():
    kb = [
        [InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="admin_add")],
        [InlineKeyboardButton(text="🎬 Barcha kinolar", callback_data="admin_list_0")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Reklama yuborish", callback_data="admin_broadcast")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- FUNKSIYALAR ---
async def is_subscribed(user_id):
    if not CHANNELS: return True
    for channel in CHANNELS:
        try:
            member = await bot.get_chat_member(chat_id=channel, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                return False
        except Exception as e:
            logging.error(f"Subscription check error for {channel}: {e}")
            return False
    return True

# --- HANDLERLAR ---

@dp.message(Command("start"))
async def start_cmd(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, full_name) VALUES (?, ?, ?)",
            (message.from_user.id, message.from_user.username, message.from_user.full_name)
        )
        await db.commit()
    
    if not await is_subscribed(message.from_user.id):
        await message.answer(f"<b>Salom!</b> Botdan foydalanish uchun kanallarimizga a'zo bo'ling:", reply_markup=sub_kb())
        return
    await message.answer(f"<b>Assalomu alaykum!</b>\nKino kodini yuboring:")

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id in ADMINS:
        await message.answer("🛠 <b>Admin Paneliga xush kelibsiz:</b>", reply_markup=admin_kb())
    else:
        await message.answer("❌ Siz admin emassiz.")

@dp.callback_query(F.data == "check_sub")
async def check_sub_handler(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.delete()
        await call.message.answer("✅ Obuna tasdiqlandi. Kino kodini yuboring:")
    else:
        await call.answer("❌ Hali hamma kanallarga a'zo emassiz!", show_alert=True)

# --- KINO QO'SHISH (ADMIN) ---
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("🎬 Kinoni yuboring (Video formatida):")
    await state.set_state(AdminStates.waiting_for_movie)

@dp.message(AdminStates.waiting_for_movie, F.video)
async def process_movie_file(message: Message, state: FSMContext):
    await state.update_data(file_id=message.video.file_id, caption=message.caption)
    await message.answer("🔢 Ushbu kino uchun <b>KOD</b> kiriting (yoki 'auto' deb yozing):")
    await state.set_state(AdminStates.waiting_for_code)

@dp.message(AdminStates.waiting_for_code)
async def process_movie_code(message: Message, state: FSMContext):
    data = await state.get_data()
    code = message.text.strip()
    if code.lower() == "auto":
        code = str(random.randint(100, 99999))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT code FROM movies WHERE code = ?", (code,)) as cursor:
            if await cursor.fetchone():
                await message.answer("❌ Bu kod band! Boshqa kod kiriting:")
                return
        await db.execute(
            "INSERT INTO movies (code, file_id, caption) VALUES (?, ?, ?)",
            (code, data['file_id'], data['caption'])
        )
        await db.commit()
    await message.answer(f"✅ <b>Kino saqlandi!</b>\n📌 Kod: <code>{code}</code>", reply_markup=admin_kb())
    await state.clear()

# --- ADMIN STATISTIKA ---
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c1, db.execute("SELECT COUNT(*) FROM movies") as c2:
            u_count = (await c1.fetchone())[0]
            m_count = (await c2.fetchone())[0]
    await call.message.answer(f"📊 <b>Statistika:</b>\n\n👤 Foydalanuvchilar: {u_count}\n🎬 Kinolar: {m_count}")

# --- BARCHA KINOLAR RO'YXATI (PAGINATION) ---
@dp.callback_query(F.data.startswith("admin_list_"))
async def admin_list_movies(call: CallbackQuery):
    page_str = call.data.split("_")[-1]
    page = int(page_str) if page_str.isdigit() else 0
    limit = 10
    offset = page * limit
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT code, caption FROM movies LIMIT ? OFFSET ?", (limit, offset)) as cursor:
            movies = await cursor.fetchall()
        async with db.execute("SELECT COUNT(*) FROM movies") as cursor:
            total_movies = (await cursor.fetchone())[0]
    if not movies and page == 0:
        await call.answer("🎬 Kinolar ro'yxati bo'sh.", show_alert=True)
        return
    text = f"🎬 <b>Barcha kinolar (Sahifa {page + 1}):</b>\n\n"
    for code, caption in movies:
        title = (caption[:30] + "...") if caption and len(caption) > 30 else (caption or "Nomsiz kino")
        text += f"▪️ {title} — <code>{code}</code>\n"
    kb = []
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="◀️ Ortga", callback_data=f"admin_list_{page-1}"))
    if offset + limit < total_movies:
        nav_buttons.append(InlineKeyboardButton(text="Oldinga ▶️", callback_data=f"admin_list_{page+1}"))
    if nav_buttons:
        kb.append(nav_buttons)
    kb.append([InlineKeyboardButton(text="⬅️ Admin Panel", callback_data="back_to_admin")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(call: CallbackQuery):
    await call.message.edit_text("🛠 <b>Admin Paneliga xush kelibsiz:</b>", reply_markup=admin_kb())

# --- REKLAMA YUBORISH ---
@dp.callback_query(F.data == "admin_broadcast")
async def broadcast_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("📢 Reklama xabarini yuboring:")
    await state.set_state(AdminStates.waiting_for_ad)

@dp.message(AdminStates.waiting_for_ad)
async def process_broadcast(message: Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users") as cursor:
            users = await cursor.fetchall()
    count = 0
    progress_msg = await message.answer("⏳ Reklama yuborilmoqda...")
    for user in users:
        try:
            await message.copy_to(chat_id=user[0])
            count += 1
            await asyncio.sleep(0.05)
        except Exception: pass
    await progress_msg.delete()
    await message.answer(f"✅ Reklama {count} kishiga yuborildi.", reply_markup=admin_kb())

# --- KINO QIDIRISH (FOYDALANUVCHI) ---
@dp.message()
async def search_movie(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer("Botdan foydalanish uchun kanallarimizga a'zo bo'ling!", reply_markup=sub_kb())
        return
    query = message.text.strip()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT file_id, caption, views FROM movies WHERE code = ?", (query,)) as cursor:
            movie = await cursor.fetchone()
        if movie:
            file_id, caption, views = movie
            await message.answer_video(video=file_id, caption=f"{caption or ''}\n\n👁 Ko'rishlar: {views + 1}")
            await db.execute("UPDATE movies SET views = views + 1 WHERE code = ?", (query,))
            await db.commit()
            return
        async with db.execute("SELECT code, caption FROM movies WHERE caption LIKE ? LIMIT 5", (f"%{query}%",)) as cursor:
            results = await cursor.fetchall()
        if results:
            text = "🔍 <b>Topilgan kinolar:</b>\n\n"
            for code, caption in results:
                title = (caption[:30] + "...") if caption and len(caption) > 30 else (caption or "Nomsiz kino")
                text += f"🎬 {title} — Kod: <code>{code}</code>\n"
            await message.answer(text)
        else:
            await message.answer("❌ Hech narsa topilmadi. Kodni to'g'ri kiriting.")

# --- ISHGA TUSHIRISH ---
async def main():
    await init_db()
    logging.basicConfig(level=logging.INFO)
    await bot.set_my_commands([
        BotCommand(command="start", description="Ishga tushirish"),
        BotCommand(command="admin", description="Admin panel")
    ])
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
