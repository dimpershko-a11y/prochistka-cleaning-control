import asyncio
import base64
import os
from aiogram import Bot
from aiogram.types import BufferedInputFile
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv('BOT_TOKEN', '').strip()
PNG_1PX = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
    'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
)

async def main():
    if not TOKEN:
        raise SystemExit('BOT_TOKEN_MISSING')
    from bot import init_db, get_setting
    init_db()
    channel_id = get_setting('archive_channel_id')
    owner_id = get_setting('owner_id')
    if not channel_id or not owner_id:
        raise SystemExit('RUNTIME_BINDINGS_MISSING')
    bot = Bot(TOKEN)
    me = await bot.get_me()
    await bot.get_chat(channel_id)
    member = await bot.get_chat_member(channel_id, me.id)
    can_post = getattr(member, 'can_post_messages', None)
    text_msg = await bot.send_message(channel_id, 'PRO-CHISTKA system check')
    await bot.delete_message(channel_id, text_msg.message_id)
    photo_msg = await bot.send_photo(
        channel_id, BufferedInputFile(PNG_1PX, filename='system_check.png'),
        caption='PRO-CHISTKA photo archive check')
    await bot.delete_message(channel_id, photo_msg.message_id)
    private_msg = await bot.send_message(int(owner_id), 'PRO-CHISTKA private chat check')
    await bot.delete_message(int(owner_id), private_msg.message_id)
    await bot.session.close()
    print('BOT_API_OK')
    print('OWNER_CHAT_OK')
    print('ARCHIVE_CHAT_OK')
    print('ARCHIVE_TEXT_OK')
    print('ARCHIVE_PHOTO_OK')
    print('CAN_POST=' + str(can_post))

asyncio.run(main())
