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
    CallbackQuery,
    InputMediaVideo
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

# --- KONFIGURATSIYA ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [int(i.strip()) for i in os.getenv("ADMINS", str(os.getenv("ADMIN_ID", ""))).split(",") if i.strip()]
CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/nexora_startup")

if not TOKEN:
    raise ValueError("BOT_TOKEN topilmadi!")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# --- MA'LUMOTLAR BAZASI ---
DB_PATH = "kinochi.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS movies (
                code TEXT PRIMARY KEY,
                file_id TEXT,
                caption TEXT,
                views INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Eski bazadan migrate qilish (agar kerak bo'lsa)
        try:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
            await db.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
            await db.execute("ALTER TABLE users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except: pass
        
        try:
            await db.execute("ALTER TABLE movies ADD COLUMN views INTEGER DEFAULT 0")
            await db.execute("ALTER TABLE movies ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
        except: pass
        
        await db.commit()

# --- HOLATLAR ---
class AdminStates(StatesGroup):
    waiting_for_movie = State()
    waiting_for_code = State()
    waiting_for_ad = State()
    waiting_for_search = State()

# --- TUGMALAR ---
def sub_kb():
    kb = [
        [InlineKeyboardButton(text="📢 Kanalga obuna bo'lish", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_kb():
    kb = [
        [InlineKeyboardButton(text="➕ Kino qo'shish", callback_data="admin_add")],
        [InlineKeyboardButton(text="🎬 Kinolar ro'yxati", callback_data="admin_list")],
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Reklama", callback_data="admin_broadcast")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def main_menu_kb():
    kb = [
        [InlineKeyboardButton(text="🎲 Tasodifiy kino", callback_data="random_movie")],
        [InlineKeyboardButton(text="🔍 Qidirish", callback_data="user_search")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

# --- FUNKSIYALAR ---
async def is_subscribed(user_id):
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        return member.status in ["member", "administrator", "creator"]
    except Exception as e:
        logging.error(f"Subscription check error: {e}")
        return False

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
        await message.answer(
            f"<b>Salom {message.from_user.full_name}!</b>\n\n"
            f"Botdan foydalanish uchun quyidagi kanalga a'zo bo'ling:",
            reply_markup=sub_kb()
        )
        return

    await message.answer(
        f"<b>Assalomu alaykum {message.from_user.full_name}!</b>\n\n"
        f"Kino kodini yuboring yoki quyidagi menyudan foydalaning:",
        reply_markup=main_menu_kb()
    )

@dp.callback_query(F.data == "check_sub")
async def check_sub_handler(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.delete()
        await call.message.answer(
            "<b>Tabriklaymiz!</b> Obuna tasdiqlandi.\nKino kodini yuboring:",
            reply_markup=main_menu_kb()
        )
    else:
        await call.answer("Barcha kanallarga a'zo bo'lishingiz shart!", show_alert=True)

@dp.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id in ADMINS:
        await message.answer("🛠 <b>Admin Boshqaruv Paneli:</b>", reply_markup=admin_kb())
    else:
        await message.answer("❌ Bu buyruq faqat admin uchun.")

# --- ADMIN: KINO QO'SHISH ---
@dp.callback_query(F.data == "admin_add")
async def admin_add_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("🎬 Kino faylini yuboring (Video):")
    await state.set_state(AdminStates.waiting_for_movie)

@dp.message(AdminStates.waiting_for_movie, F.video)
async def process_movie_file(message: Message, state: FSMContext):
    await state.update_data(file_id=message.video.file_id, caption=message.caption)
    await message.answer("🔢 Kino uchun kod kiriting (yoki 'auto' deb yozing tasodifiy kod uchun):")
    await state.set_state(AdminStates.waiting_for_code)

@dp.message(AdminStates.waiting_for_code)
async def process_movie_code(message: Message, state: FSMContext):
    data = await state.get_data()
    code = message.text.strip()
    
    if code.lower() == "auto":
        code = str(random.randint(1000, 9999))
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Kod bandligini tekshirish
        async with db.execute("SELECT code FROM movies WHERE code = ?", (code,)) as cursor:
            if await cursor.fetchone():
                await message.answer("❌ Bu kod band! Boshqa kod kiriting:")
                return
        
        await db.execute(
            "INSERT INTO movies (code, file_id, caption) VALUES (?, ?, ?)",
            (code, data['file_id'], data['caption'])
        )
        await db.commit()
    
    await message.answer(f"✅ <b>Kino saqlandi!</b>\n\n📌 Kodi: <code>{code}</code>", reply_markup=admin_kb())
    await state.clear()

# --- ADMIN: STATISTIKA ---
@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM movies") as c:
            movies_count = (await c.fetchone())[0]
        async with db.execute("SELECT SUM(views) FROM movies") as c:
            total_views = (await c.fetchone())[0] or 0
            
    text = (
        f"📊 <b>Statistika:</b>\n\n"
        f"👤 Foydalanuvchilar: <b>{users_count}</b>\n"
        f"🎬 Kinolar soni: <b>{movies_count}</b>\n"
        f"👁 Umumiy ko'rishlar: <b>{total_views}</b>"
    )
    await call.message.edit_text(text, reply_markup=admin_kb())

# --- ADMIN: REKLAMA ---
@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("📢 Reklama xabarini yuboring (matn, rasm yoki video):")
    await state.set_state(AdminStates.waiting_for_ad)

@dp.message(AdminStates.waiting_for_ad)
async def process_broadcast(message: Message, state: FSMContext):
    await state.clear()
    status_msg = await message.answer("⏳ Reklama yuborish jarayoni boshlandi...")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM users") as cursor:
            users = await cursor.fetchall()
            
    success = 0
    fail = 0
    total = len(users)
    
    for user in users:
        try:
            await message.copy_to(chat_id=user[0])
            success += 1
        except (TelegramForbiddenError, Exception):
            fail += 1
        
        # Har 50 ta foydalanuvchida yangilash
        if (success + fail) % 50 == 0:
            try:
                await status_msg.edit_text(f"⏳ Yuborilyapti: {success + fail}/{total}\n✅ Muvaffaqiyatli: {success}\n❌ Taqiqlandi: {fail}")
            except: pass
            
        await asyncio.sleep(0.05) # Flood wait oldini olish
        
    await status_msg.edit_text(
        f"✅ <b>Reklama yakunlandi!</b>\n\n"
        f"📊 Jami: {total}\n"
        f"✅ Yuborildi: {success}\n"
        f"❌ Yuborilmadi: {fail}"
    )

# --- ADMIN: KINOLAR RO'YXATI ---
@dp.callback_query(F.data == "admin_list")
async def admin_list_movies(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT code, caption FROM movies ORDER BY created_at DESC LIMIT 10") as cursor:
            movies = await cursor.fetchall()
            
    if not movies:
        await call.answer("Hozircha kinolar yo'q.", show_alert=True)
        return
        
    text = "🎬 <b>Oxirgi 10 ta kino:</b>\n\n"
    kb = []
    for code, caption in movies:
        title = (caption[:30] + "...") if caption and len(caption) > 30 else (caption or "Nomsiz")
        text += f"📌 <code>{code}</code> - {title}\n"
        kb.append([InlineKeyboardButton(text=f"🗑 {code} ni o'chirish", callback_data=f"del_{code}")])
    
    kb.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data="admin_back")])
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("del_"))
async def delete_movie(call: CallbackQuery):
    code = call.data.split("_")[1]
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM movies WHERE code = ?", (code,))
        await db.commit()
    await call.answer("❌ Kino o'chirildi!", show_alert=True)
    await admin_list_movies(call)

@dp.callback_query(F.data == "admin_back")
async def admin_back(call: CallbackQuery):
    await call.message.edit_text("🛠 <b>Admin Boshqaruv Paneli:</b>", reply_markup=admin_kb())

# --- FOYDALANUVCHI: QIDIRUV VA TASODIFIY ---

@dp.callback_query(F.data == "random_movie")
async def random_movie_handler(call: CallbackQuery):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT file_id, caption, code FROM movies ORDER BY RANDOM() LIMIT 1") as cursor:
            movie = await cursor.fetchone()
            
    if movie:
        file_id, caption, code = movie
        text = f"{caption}\n\n🎬 Kod: <code>{code}</code>" if caption else f"🎬 Kod: <code>{code}</code>"
        await call.message.answer_video(video=file_id, caption=text)
        await db.execute("UPDATE movies SET views = views + 1 WHERE code = ?", (code,))
        await db.commit()
    else:
        await call.answer("Hozircha kinolar yo'q.", show_alert=True)

@dp.message()
async def universal_search(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer("Botdan foydalanish uchun kanalga a'zo bo'ling!", reply_markup=sub_kb())
        return

    query = message.text.strip()
    
    async with aiosqlite.connect(DB_PATH) as db:
        # Kod bo'yicha qidirish
        async with db.execute("SELECT file_id, caption, code FROM movies WHERE code = ?", (query,)) as cursor:
            movie = await cursor.fetchone()
            
        if movie:
            file_id, caption, code = movie
            text = f"{caption}\n\n🎬 Kod: <code>{code}</code>" if caption else f"🎬 Kod: <code>{code}</code>"
            await message.answer_video(video=file_id, caption=text)
            await db.execute("UPDATE movies SET views = views + 1 WHERE code = ?", (code,))
            await db.commit()
            return
            
        # Kalit so'z bo'yicha qidirish
        async with db.execute("SELECT code, caption FROM movies WHERE caption LIKE ? LIMIT 5", (f"%{query}%",)) as cursor:
            results = await cursor.fetchall()
            
        if results:
            text = "🔍 <b>Qidiruv natijalari:</b>\n\n"
            kb = []
            for code, caption in results:
                title = (caption[:40] + "...") if caption and len(caption) > 40 else (caption or "Kino")
                text += f"🎬 {title}\n📌 Kod: <code>{code}</code>\n\n"
            await message.answer(text, reply_markup=main_menu_kb())
        else:
            await message.answer("❌ Hech narsa topilmadi. Kino kodini yoki nomini to'g'ri kiriting.")

# --- ISH BOTNI YURGIZISH ---
async def set_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Botni ishga tushirish"),
        BotCommand(command="help", description="Yordam"),
        BotCommand(command="admin", description="Admin paneli")
    ]
    await bot.set_my_commands(commands)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    await set_commands(bot)
    logging.info("Bot ishga tushdi...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot to'xtatildi")
