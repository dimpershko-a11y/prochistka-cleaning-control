import asyncio
import html
import math
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove
from dotenv import load_dotenv

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN', '').strip()
OWNER_ENV = int(os.getenv('OWNER_TELEGRAM_ID', '0') or 0)
ARCHIVE_ENV = os.getenv('ARCHIVE_CHANNEL_ID', '').strip()
MINIAPP_URL = os.getenv('MINIAPP_URL', '').strip()
DB_PATH = Path(os.getenv('DB_PATH', 'data/prochistka_control.db'))
BEFORE_MIN = int(os.getenv('BEFORE_MIN_PHOTOS', '4'))
AFTER_MIN = int(os.getenv('AFTER_MIN_PHOTOS', '4'))
GEOFENCE_OK = int(os.getenv('GEOFENCE_OK_M', '150'))
GEOFENCE_WARN = int(os.getenv('GEOFENCE_WARN_M', '300'))
router = Router()
def now_iso():
    return datetime.now().astimezone().isoformat(timespec='seconds')


def today_str():
    return datetime.now().astimezone().date().isoformat()


def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT, public_number TEXT UNIQUE,
          address TEXT NOT NULL, latitude REAL, longitude REAL,
          scheduled_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PLANNED',
          consent TEXT NOT NULL DEFAULT 'UNKNOWN', arrived_at TEXT,
          arrival_lat REAL, arrival_lon REAL, arrival_accuracy REAL,
          arrival_distance_m REAL, started_at TEXT, finished_at TEXT,
          created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS media(
          id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
          stage TEXT NOT NULL, media_type TEXT NOT NULL, file_id TEXT NOT NULL,
          file_unique_id TEXT, source_chat_id INTEGER, source_message_id INTEGER,
          archive_chat_id TEXT, archive_message_id INTEGER, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          user_id INTEGER PRIMARY KEY, active_order_id INTEGER,
          expected_action TEXT, expected_media_stage TEXT, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS status_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
          from_status TEXT, to_status TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
        ''')


def get_setting(key):
    with db() as c:
        row = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return row['value'] if row else None


def set_setting(key, value):
    with db() as c:
        c.execute('INSERT INTO settings(key,value) VALUES(?,?) '
                  'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def owner_id():
    return OWNER_ENV or int(get_setting('owner_id') or 0)


def archive_id():
    return ARCHIVE_ENV or (get_setting('archive_channel_id') or '')


def is_owner(user_id):
    return user_id == owner_id() and owner_id() != 0
def get_session(user_id):
    with db() as c:
        row = c.execute('SELECT * FROM sessions WHERE user_id=?', (user_id,)).fetchone()
        if row:
            return row
        c.execute('INSERT INTO sessions(user_id,updated_at) VALUES(?,?)', (user_id, now_iso()))
        return c.execute('SELECT * FROM sessions WHERE user_id=?', (user_id,)).fetchone()


def set_session(user_id, order_id=None, action=None, media_stage=None):
    get_session(user_id)
    with db() as c:
        c.execute('UPDATE sessions SET active_order_id=?, expected_action=?, '
                  'expected_media_stage=?, updated_at=? WHERE user_id=?',
                  (order_id, action, media_stage, now_iso(), user_id))


def get_order(order_id):
    with db() as c:
        return c.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()


def set_status(order_id, new_status, note=None):
    order = get_order(order_id)
    if not order:
        return
    with db() as c:
        c.execute('UPDATE orders SET status=? WHERE id=?', (new_status, order_id))
        c.execute('INSERT INTO status_history(order_id,from_status,to_status,note,created_at) '
                  'VALUES(?,?,?,?,?)', (order_id, order['status'], new_status, note, now_iso()))


def count_photos(order_id, stage):
    with db() as c:
        return c.execute("SELECT COUNT(*) n FROM media WHERE order_id=? AND stage=? AND media_type='PHOTO'",
                         (order_id, stage)).fetchone()['n']
def create_order(address):
    with db() as c:
        cur = c.execute("INSERT INTO orders(address,scheduled_date,status,created_at) VALUES(?,?,'PLANNED',?)",
                        (address.strip(), today_str(), now_iso()))
        oid = cur.lastrowid
        num = f"T{datetime.now().strftime('%m%d')}-{oid}"
        c.execute('UPDATE orders SET public_number=? WHERE id=?', (num, oid))
    return get_order(oid)


def set_order_coords(order_id, lat, lon):
    with db() as c:
        c.execute('UPDATE orders SET latitude=?, longitude=? WHERE id=?', (lat, lon, order_id))


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*r*math.asin(math.sqrt(a))


def main_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text='📅 Сегодня')],
        [KeyboardButton(text='➕ Тестовый заказ')]
    ], resize_keyboard=True)


def location_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[
        KeyboardButton(text='📍 Отправить геолокацию', request_location=True)
    ]], resize_keyboard=True, one_time_keyboard=True)
def order_keyboard(order):
    oid, st = order['id'], order['status']
    rows = [[InlineKeyboardButton(text='🗺 Маршрут', callback_data=f'route:{oid}')]]
    if st == 'PLANNED':
        rows.append([InlineKeyboardButton(text='📍 Я приехал', callback_data=f'arrive:{oid}')])
    elif st == 'READY_TO_START':
        rows.append([InlineKeyboardButton(text='▶️ Начать уборку', callback_data=f'start:{oid}')])
    elif st == 'IN_PROGRESS':
        rows.append([InlineKeyboardButton(text='✅ Завершить работу', callback_data=f'finish:{oid}')])
    elif st == 'READY_TO_COMPLETE':
        rows.append([InlineKeyboardButton(text='✅ Закрыть заказ', callback_data=f'complete:{oid}')])
    rows.append([InlineKeyboardButton(text='🔄 Обновить', callback_data=f'order:{oid}')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_text(order):
    return (
        f"<b>Заказ {html.escape(order['public_number'])}</b>\n"
        f"📍 {html.escape(order['address'])}\n"
        f"Статус: <b>{order['status']}</b>\n"
        f"Согласие: <b>{order['consent']}</b>\n\n"
        f"Фото ДО: {count_photos(order['id'], 'BEFORE')}/{BEFORE_MIN}\n"
        f"Фото ПОСЛЕ: {count_photos(order['id'], 'AFTER')}/{AFTER_MIN}\n"
        f"Прибытие: {order['arrived_at'] or '—'}\n"
        f"Начало: {order['started_at'] or '—'}\n"
        f"Окончание: {order['finished_at'] or '—'}"
    )


async def show_order(message, order_id):
    order = get_order(order_id)
    if order:
        await message.answer(order_text(order), reply_markup=order_keyboard(order))
async def archive_media(bot, message, order, stage, media_type, file_id, unique_id):
    channel = archive_id()
    archive_message_id = None
    if channel:
        caption = f"#{order['public_number']} | {stage} | {order['address']}"
        try:
            if media_type == 'PHOTO':
                sent = await bot.send_photo(channel, file_id, caption=caption)
            else:
                sent = await bot.send_video(channel, file_id, caption=caption)
            archive_message_id = sent.message_id
        except Exception as exc:
            await message.answer(f'⚠️ Архив недоступен: {type(exc).__name__}')
    with db() as c:
        c.execute('''INSERT INTO media(order_id,stage,media_type,file_id,file_unique_id,
                   source_chat_id,source_message_id,archive_chat_id,archive_message_id,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)''',
                  (order['id'], stage, media_type, file_id, unique_id,
                   message.chat.id, message.message_id, channel or None,
                   archive_message_id, now_iso()))


@router.channel_post(F.text == '#bind_archive')
async def bind_archive(message: Message):
    set_setting('archive_channel_id', message.chat.id)
    await message.answer('✅ Архивный канал подключён.')


@router.message(CommandStart())
async def start(message: Message):
    current = owner_id()
    if current == 0:
        set_setting('owner_id', message.from_user.id)
        current = message.from_user.id
    if message.from_user.id != current:
        await message.answer('Доступ запрещён.')
        return
    get_session(current)
    await message.answer('PRO-CHISTKA Control Bot', reply_markup=main_keyboard())
@router.message(F.text == '➕ Тестовый заказ')
async def new_order(message: Message):
    if not is_owner(message.from_user.id):
        return
    set_session(message.from_user.id, None, 'NEW_ORDER_ADDRESS', None)
    await message.answer('Введите адрес объекта:', reply_markup=ReplyKeyboardRemove())


@router.message(F.text == '📅 Сегодня')
async def today(message: Message):
    if not is_owner(message.from_user.id):
        return
    with db() as c:
        rows = c.execute('SELECT * FROM orders WHERE scheduled_date=? ORDER BY id',
                         (today_str(),)).fetchall()
    if not rows:
        await message.answer('На сегодня заказов нет.', reply_markup=main_keyboard())
        return
    for order in rows:
        await message.answer(order_text(order), reply_markup=order_keyboard(order))


@router.callback_query(F.data.startswith('order:'))
async def cb_order(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    set_session(call.from_user.id, oid, None, None)
    await call.answer()
    await show_order(call.message, oid)


@router.callback_query(F.data.startswith('route:'))
async def cb_route(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    order = get_order(int(call.data.split(':')[1]))
    if not order:
        return
    q = quote(order['address'])
    rows = [[InlineKeyboardButton(text='Яндекс Карты', url=f'https://yandex.ru/maps/?text={q}')],
            [InlineKeyboardButton(text='2ГИС', url=f'https://2gis.ru/search/{q}')],
            [InlineKeyboardButton(text='Google Maps', url=f'https://www.google.com/maps/dir/?api=1&destination={q}')]]
    await call.answer()
    await call.message.answer('Выберите навигацию:', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
@router.callback_query(F.data.startswith('arrive:'))
async def cb_arrive(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    order = get_order(oid)
    if not order or order['status'] != 'PLANNED':
        await call.answer('Этап уже пройден.', show_alert=True)
        return
    set_session(call.from_user.id, oid, 'ARRIVAL_LOCATION', None)
    await call.answer()
    await call.message.answer('Отправьте текущую геолокацию.', reply_markup=location_keyboard())


@router.message(F.location)
async def location(message: Message):
    if not is_owner(message.from_user.id):
        return
    s = get_session(message.from_user.id)
    action = s['expected_action']
    if action == 'NEW_ORDER_LOCATION':
        set_order_coords(s['active_order_id'], message.location.latitude, message.location.longitude)
        set_session(message.from_user.id, s['active_order_id'], None, None)
        await message.answer('Точка объекта сохранена ✅', reply_markup=main_keyboard())
        await show_order(message, s['active_order_id'])
        return
    if action != 'ARRIVAL_LOCATION':
        await message.answer('Сейчас геолокация не запрашивается.')
        return
    order = get_order(s['active_order_id'])
    distance = None
    if order['latitude'] is not None and order['longitude'] is not None:
        distance = haversine_m(message.location.latitude, message.location.longitude,
                               order['latitude'], order['longitude'])
    accuracy = getattr(message.location, 'horizontal_accuracy', None)
    with db() as c:
        c.execute('''UPDATE orders SET arrived_at=?, arrival_lat=?, arrival_lon=?,
                     arrival_accuracy=?, arrival_distance_m=? WHERE id=?''',
                  (now_iso(), message.location.latitude, message.location.longitude,
                   accuracy, distance, order['id']))
    set_status(order['id'], 'ARRIVED', 'arrival_location')
    set_session(message.from_user.id, order['id'], None, None)
    text = 'Прибытие зафиксировано ✅'
    if distance is not None:
        text += f'\nРасстояние до точки: {round(distance)} м.'
        if distance > GEOFENCE_WARN:
            text += '\n⚠️ Вы далеко от сохранённой точки объекта.'
        elif distance > GEOFENCE_OK:
            text += '\n⚠️ Проверьте, что выбран правильный объект.'
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Да, согласие подписано', callback_data=f'consent_yes:{order["id"]}')],
        [InlineKeyboardButton(text='🚫 Нет, съёмка запрещена', callback_data=f'consent_no:{order["id"]}')]
    ])
    await message.answer(text + '\n\nКлиент разрешил фото/видео съёмку?',
                         reply_markup=kb)


@router.callback_query(F.data.startswith('consent_yes:'))
async def consent_yes(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    order = get_order(oid)
    if not order or order['status'] != 'ARRIVED':
        await call.answer('Этап уже пройден.', show_alert=True)
        return
    with db() as c:
        c.execute("UPDATE orders SET consent='ALLOWED' WHERE id=?", (oid,))
    set_status(oid, 'BEFORE_REQUIRED', 'consent_allowed')
    set_session(call.from_user.id, oid, None, 'BEFORE')
    await call.answer()
    await call.message.answer(f'Согласие зафиксировано ✅\nОтправьте минимум {BEFORE_MIN} фото ДО.')


@router.callback_query(F.data.startswith('consent_no:'))
async def consent_no(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    order = get_order(oid)
    if not order or order['status'] != 'ARRIVED':
        await call.answer('Этап уже пройден.', show_alert=True)
        return
    with db() as c:
        c.execute("UPDATE orders SET consent='DENIED' WHERE id=?", (oid,))
    set_status(oid, 'READY_TO_START', 'consent_denied')
    set_session(call.from_user.id, oid, None, None)
    await call.answer()
    await call.message.answer('Съёмка запрещена. Фото не требуются.', reply_markup=order_keyboard(get_order(oid)))
@router.message(F.photo | F.video)
async def media(message: Message, bot: Bot):
    if not is_owner(message.from_user.id):
        return
    s = get_session(message.from_user.id)
    oid, stage = s['active_order_id'], s['expected_media_stage']
    if not oid or stage not in ('BEFORE', 'AFTER'):
        await message.answer('Сейчас бот не ожидает фото/видео.')
        return
    order = get_order(oid)
    if not order or order['consent'] != 'ALLOWED':
        await message.answer('Съёмка для этого заказа не разрешена.')
        return
    if message.photo:
        item = message.photo[-1]
        mtype, fid, uid = 'PHOTO', item.file_id, item.file_unique_id
    else:
        item = message.video
        mtype, fid, uid = 'VIDEO', item.file_id, item.file_unique_id
    await archive_media(bot, message, order, stage, mtype, fid, uid)
    if mtype == 'VIDEO':
        await message.answer(f'Видео {stage} сохранено ✅')
        return
    count = count_photos(oid, stage)
    need = BEFORE_MIN if stage == 'BEFORE' else AFTER_MIN
    if count < need:
        await message.answer(f'Фото {stage}: {count}/{need}')
        return
    if stage == 'BEFORE' and order['status'] == 'BEFORE_REQUIRED':
        set_status(oid, 'READY_TO_START', 'before_complete')
        set_session(message.from_user.id, oid, None, None)
        await message.answer(f'Фото ДО: {count}/{need} ✅', reply_markup=order_keyboard(get_order(oid)))
    elif stage == 'AFTER' and order['status'] == 'AFTER_REQUIRED':
        set_status(oid, 'READY_TO_COMPLETE', 'after_complete')
        set_session(message.from_user.id, oid, None, None)
        await message.answer(f'Фото ПОСЛЕ: {count}/{need} ✅', reply_markup=order_keyboard(get_order(oid)))
@router.callback_query(F.data.startswith('start:'))
async def start_cleaning(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    order = get_order(oid)
    if not order or order['status'] != 'READY_TO_START':
        await call.answer('Уборку сейчас начать нельзя.', show_alert=True)
        return
    with db() as c:
        c.execute('UPDATE orders SET started_at=? WHERE id=?', (now_iso(), oid))
    set_status(oid, 'IN_PROGRESS')
    set_session(call.from_user.id, oid, None, None)
    await call.answer('Уборка начата.')
    await show_order(call.message, oid)


@router.callback_query(F.data.startswith('finish:'))
async def finish_cleaning(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    order = get_order(oid)
    if not order or order['status'] != 'IN_PROGRESS':
        await call.answer('Этап уже пройден.', show_alert=True)
        return
    if order['consent'] == 'ALLOWED':
        set_status(oid, 'AFTER_REQUIRED')
        set_session(call.from_user.id, oid, None, 'AFTER')
        await call.answer()
        await call.message.answer(f'Отправьте минимум {AFTER_MIN} фото ПОСЛЕ.')
    else:
        set_status(oid, 'READY_TO_COMPLETE', 'media_not_required')
        set_session(call.from_user.id, oid, None, None)
        await call.answer()
        await call.message.answer('Фото не требуются. Можно закрыть заказ.',
                                  reply_markup=order_keyboard(get_order(oid)))


@router.callback_query(F.data.startswith('complete:'))
async def complete(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    order = get_order(oid)
    if not order or order['status'] != 'READY_TO_COMPLETE':
        await call.answer('Заказ пока нельзя закрыть.', show_alert=True)
        return
    with db() as c:
        c.execute('UPDATE orders SET finished_at=? WHERE id=?', (now_iso(), oid))
    set_status(oid, 'COMPLETED')
    set_session(call.from_user.id, None, None, None)
    await call.answer('Заказ завершён.')
    await show_order(call.message, oid)
    await call.message.answer('Готово ✅', reply_markup=main_keyboard())
@router.callback_query(F.data.startswith('skip_location:'))
async def skip_location(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    oid = int(call.data.split(':')[1])
    set_session(call.from_user.id, oid, None, None)
    await call.answer()
    await call.message.answer('Геоточка объекта пропущена.', reply_markup=main_keyboard())
    await show_order(call.message, oid)


@router.message(F.text)
async def text_input(message: Message):
    if not is_owner(message.from_user.id):
        return
    s = get_session(message.from_user.id)
    if s['expected_action'] != 'NEW_ORDER_ADDRESS':
        await message.answer('Используйте кнопки меню.', reply_markup=main_keyboard())
        return
    order = create_order(message.text)
    set_session(message.from_user.id, order['id'], 'NEW_ORDER_LOCATION', None)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text='Пропустить геоточку', callback_data=f'skip_location:{order["id"]}')
    ]])
    await message.answer('Заказ создан ✅\nОтправьте геоточку объекта или пропустите.', reply_markup=kb)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError('BOT_TOKEN не заполнен в .env')
    init_db()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
