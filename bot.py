import asyncio
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
OWNER_ID = int(os.getenv('OWNER_TELEGRAM_ID', '0') or 0)
ARCHIVE_CHANNEL_ID = os.getenv('ARCHIVE_CHANNEL_ID', '').strip()
DB_PATH = Path(os.getenv('DB_PATH', 'data/prochistka_control.db'))
BEFORE_MIN = int(os.getenv('BEFORE_MIN_PHOTOS', '4'))
AFTER_MIN = int(os.getenv('AFTER_MIN_PHOTOS', '4'))
router = Router()


def now(): return datetime.now().astimezone().isoformat(timespec='seconds')
def today(): return datetime.now().astimezone().date().isoformat()

def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          address TEXT NOT NULL, day TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'PLANNED',
          consent TEXT NOT NULL DEFAULT 'UNKNOWN',
          arrived_at TEXT, started_at TEXT, finished_at TEXT);
        CREATE TABLE IF NOT EXISTS media(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL, stage TEXT NOT NULL,
          kind TEXT NOT NULL, file_id TEXT NOT NULL,
          archive_message_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          user_id INTEGER PRIMARY KEY, active_order_id INTEGER,
          action TEXT, media_stage TEXT);
        ''')

def allowed(uid): return OWNER_ID != 0 and uid == OWNER_ID

def session(uid):
    with db() as c:
        r = c.execute('SELECT * FROM sessions WHERE user_id=?', (uid,)).fetchone()
        if not r:
            c.execute('INSERT INTO sessions(user_id) VALUES(?)', (uid,))
            r = c.execute('SELECT * FROM sessions WHERE user_id=?', (uid,)).fetchone()
        return r

def set_session(uid, order_id=None, action=None, media_stage=None):
    session(uid)
    with db() as c:
        c.execute('UPDATE sessions SET active_order_id=?, action=?, media_stage=? WHERE user_id=?',
                  (order_id, action, media_stage, uid))

def order(oid):
    with db() as c: return c.execute('SELECT * FROM orders WHERE id=?', (oid,)).fetchone()

def set_status(oid, status):
    with db() as c: c.execute('UPDATE orders SET status=? WHERE id=?', (status, oid))

def photos(oid, stage):
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM media WHERE order_id=? AND stage=? AND kind='PHOTO'", (oid, stage)).fetchone()['n']

def main_menu():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='📅 Сегодня')],[KeyboardButton(text='➕ Тестовый заказ')]], resize_keyboard=True)

def order_kb(o):
    oid, st = o['id'], o['status']; rows=[]
    if st == 'PLANNED': rows.append([InlineKeyboardButton(text='📍 Я приехал', callback_data=f'arrive:{oid}')])
    if st == 'READY_TO_START': rows.append([InlineKeyboardButton(text='▶️ Начать уборку', callback_data=f'start:{oid}')])
    if st == 'IN_PROGRESS': rows.append([InlineKeyboardButton(text='✅ Завершить работу', callback_data=f'finish:{oid}')])
    if st == 'READY_TO_COMPLETE': rows.append([InlineKeyboardButton(text='✅ Закрыть заказ', callback_data=f'complete:{oid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def order_text(o):
    return (f"<b>Заказ #{o['id']}</b>\n📍 {o['address']}\nСтатус: <b>{o['status']}</b>\n"
            f"Согласие: <b>{o['consent']}</b>\nФото ДО: {photos(o['id'],'BEFORE')}/{BEFORE_MIN}\n"
            f"Фото ПОСЛЕ: {photos(o['id'],'AFTER')}/{AFTER_MIN}")

async def show(message, oid):
    o=order(oid)
    if o: await message.answer(order_text(o), reply_markup=order_kb(o))

@router.message(CommandStart())
async def cmd_start(m: Message):
    if OWNER_ID == 0:
        await m.answer(f'Ваш Telegram ID: <code>{m.from_user.id}</code>\nЗапишите его в OWNER_TELEGRAM_ID и перезапустите бота.')
        return
    if not allowed(m.from_user.id): return
    await m.answer('PRO-CHISTKA Control Bot', reply_markup=main_menu())

@router.message(F.text == '➕ Тестовый заказ')
async def new_order(m: Message):
    if not allowed(m.from_user.id): return
    set_session(m.from_user.id, action='NEW_ADDRESS')
    await m.answer('Введите адрес объекта:')

@router.message(F.text == '📅 Сегодня')
async def list_today(m: Message):
    if not allowed(m.from_user.id): return
    with db() as c: rows=c.execute('SELECT * FROM orders WHERE day=? ORDER BY id', (today(),)).fetchall()
    if not rows: await m.answer('На сегодня заказов нет.', reply_markup=main_menu()); return
    for o in rows: await show(m, o['id'])

@router.callback_query(F.data.startswith('arrive:'))
async def arrive(cq: CallbackQuery):
    if not allowed(cq.from_user.id): return
    oid=int(cq.data.split(':')[1]); o=order(oid)
    if not o or o['status']!='PLANNED': await cq.answer('Этап уже пройден', show_alert=True); return
    with db() as c: c.execute('UPDATE orders SET arrived_at=? WHERE id=?', (now(),oid))
    set_status(oid,'ARRIVED'); set_session(cq.from_user.id,oid)
    kb=InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text='✅ Да, согласие подписано', callback_data=f'yes:{oid}')],
      [InlineKeyboardButton(text='🚫 Нет, съёмка запрещена', callback_data=f'no:{oid}')]])
    await cq.answer(); await cq.message.answer('Прибытие зафиксировано. Клиент подписал согласие на фото/видео?', reply_markup=kb)

@router.callback_query(F.data.startswith('yes:'))
async def consent_yes(cq: CallbackQuery):
    oid=int(cq.data.split(':')[1]); o=order(oid)
    if not allowed(cq.from_user.id) or not o or o['status']!='ARRIVED': return
    with db() as c: c.execute("UPDATE orders SET consent='ALLOWED', status='BEFORE_REQUIRED' WHERE id=?",(oid,))
    set_session(cq.from_user.id,oid,media_stage='BEFORE')
    await cq.answer(); await cq.message.answer(f'Отправьте минимум {BEFORE_MIN} фото ДО уборки.')

@router.callback_query(F.data.startswith('no:'))
async def consent_no(cq: CallbackQuery):
    oid=int(cq.data.split(':')[1]); o=order(oid)
    if not allowed(cq.from_user.id) or not o or o['status']!='ARRIVED': return
    with db() as c: c.execute("UPDATE orders SET consent='DENIED', status='READY_TO_START' WHERE id=?",(oid,))
    set_session(cq.from_user.id,oid)
    await cq.answer(); await show(cq.message,oid)

@router.message(F.photo | F.video)
async def media(m: Message, bot: Bot):
    if not allowed(m.from_user.id): return
    s=session(m.from_user.id); oid=s['active_order_id']; stage=s['media_stage']
    if not oid or stage not in ('BEFORE','AFTER'): await m.answer('Сейчас фото/видео не запрашиваются.'); return
    o=order(oid)
    if not o or o['consent']!='ALLOWED': await m.answer('Съёмка для заказа не разрешена.'); return
    if m.photo: kind='PHOTO'; file_id=m.photo[-1].file_id
    else: kind='VIDEO'; file_id=m.video.file_id
    archive_id=None
    if ARCHIVE_CHANNEL_ID:
        try:
            caption=f"Заказ #{oid} | {stage} | {o['address']}"
            sent=await (bot.send_photo(ARCHIVE_CHANNEL_ID,file_id,caption=caption) if kind=='PHOTO' else bot.send_video(ARCHIVE_CHANNEL_ID,file_id,caption=caption))
            archive_id=sent.message_id
        except Exception as e: await m.answer(f'⚠️ Архив: {e}')
    with db() as c: c.execute('INSERT INTO media(order_id,stage,kind,file_id,archive_message_id,created_at) VALUES(?,?,?,?,?,?)',(oid,stage,kind,file_id,archive_id,now()))
    if kind=='VIDEO': await m.answer('Видео сохранено ✅'); return
    n=photos(oid,stage); need=BEFORE_MIN if stage=='BEFORE' else AFTER_MIN
    if n<need: await m.answer(f'Фото {stage}: {n}/{need}'); return
    if stage=='BEFORE': set_status(oid,'READY_TO_START')
    else: set_status(oid,'READY_TO_COMPLETE')
    set_session(m.from_user.id,oid)
    await m.answer(f'Фото {stage}: {n}/{need} ✅', reply_markup=order_kb(order(oid)))

@router.callback_query(F.data.startswith('start:'))
async def start_clean(cq: CallbackQuery):
    oid=int(cq.data.split(':')[1]); o=order(oid)
    if not allowed(cq.from_user.id) or not o or o['status']!='READY_TO_START': return
    with db() as c: c.execute("UPDATE orders SET status='IN_PROGRESS', started_at=? WHERE id=?",(now(),oid))
    set_session(cq.from_user.id,oid); await cq.answer(); await show(cq.message,oid)

@router.callback_query(F.data.startswith('finish:'))
async def finish(cq: CallbackQuery):
    oid=int(cq.data.split(':')[1]); o=order(oid)
    if not allowed(cq.from_user.id) or not o or o['status']!='IN_PROGRESS': return
    if o['consent']=='ALLOWED':
        set_status(oid,'AFTER_REQUIRED'); set_session(cq.from_user.id,oid,media_stage='AFTER')
        await cq.answer(); await cq.message.answer(f'Отправьте минимум {AFTER_MIN} фото ПОСЛЕ уборки.')
    else:
        set_status(oid,'READY_TO_COMPLETE'); set_session(cq.from_user.id,oid)
        await cq.answer(); await show(cq.message,oid)

@router.callback_query(F.data.startswith('complete:'))
async def complete(cq: CallbackQuery):
    oid=int(cq.data.split(':')[1]); o=order(oid)
    if not allowed(cq.from_user.id) or not o or o['status']!='READY_TO_COMPLETE': return
    with db() as c: c.execute("UPDATE orders SET status='COMPLETED', finished_at=? WHERE id=?",(now(),oid))
    set_session(cq.from_user.id); await cq.answer('Готово'); await show(cq.message,oid); await cq.message.answer('Заказ завершён ✅',reply_markup=main_menu())

@router.message(F.text)
async def text(m: Message):
    if not allowed(m.from_user.id): return
    s=session(m.from_user.id)
    if s['action']!='NEW_ADDRESS': await m.answer('Используйте кнопки меню.',reply_markup=main_menu()); return
    with db() as c:
        cur=c.execute("INSERT INTO orders(address,day,status) VALUES(?,?,'PLANNED')",(m.text.strip(),today())); oid=cur.lastrowid
    set_session(m.from_user.id,oid); await m.answer('Тестовый заказ создан ✅'); await show(m,oid)

async def main():
    if not BOT_TOKEN: raise RuntimeError('BOT_TOKEN не заполнен в .env')
    init_db(); bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(); dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False); await dp.start_polling(bot)

if __name__=='__main__': asyncio.run(main())
