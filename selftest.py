import os
from pathlib import Path

os.environ['DB_PATH'] = 'data/selftest.db'
import bot

p = Path('data/selftest.db')
if p.exists():
    p.unlink()
bot.init_db()
bot.set_setting('owner_id', 123456)
assert bot.owner_id() == 123456

# Scenario 1: client refuses photo/video.
denied = bot.create_order('Test address denied')
bot.set_status(denied['id'], 'ARRIVED', 'manual_arrival')
with bot.db() as c:
    c.execute("UPDATE orders SET consent='DENIED' WHERE id=?", (denied['id'],))
bot.set_status(denied['id'], 'READY_TO_START', 'consent_denied')
bot.set_status(denied['id'], 'IN_PROGRESS')
bot.set_status(denied['id'], 'READY_TO_COMPLETE', 'media_not_required')
bot.set_status(denied['id'], 'COMPLETED')
assert bot.get_order(denied['id'])['status'] == 'COMPLETED'
assert bot.count_photos(denied['id'], 'BEFORE') == 0
assert bot.count_photos(denied['id'], 'AFTER') == 0

# Scenario 2: consent signed, 4 BEFORE + 4 AFTER photos.
allowed = bot.create_order('Test address allowed')
bot.set_status(allowed['id'], 'ARRIVED')
with bot.db() as c:
    c.execute("UPDATE orders SET consent='ALLOWED' WHERE id=?", (allowed['id'],))
bot.set_status(allowed['id'], 'BEFORE_REQUIRED', 'consent_allowed')
with bot.db() as c:
    for i in range(4):
        c.execute("INSERT INTO media(order_id,stage,media_type,file_id,created_at) VALUES(?, 'BEFORE','PHOTO',?,?)",
                  (allowed['id'], f'before-{i}', bot.now_iso()))
assert bot.count_photos(allowed['id'], 'BEFORE') == 4
bot.set_status(allowed['id'], 'READY_TO_START', 'before_complete')
bot.set_status(allowed['id'], 'IN_PROGRESS')
bot.set_status(allowed['id'], 'AFTER_REQUIRED')
with bot.db() as c:
    for i in range(4):
        c.execute("INSERT INTO media(order_id,stage,media_type,file_id,created_at) VALUES(?, 'AFTER','PHOTO',?,?)",
                  (allowed['id'], f'after-{i}', bot.now_iso()))
assert bot.count_photos(allowed['id'], 'AFTER') == 4
bot.set_status(allowed['id'], 'READY_TO_COMPLETE', 'after_complete')
bot.set_status(allowed['id'], 'COMPLETED')
assert bot.get_order(allowed['id'])['status'] == 'COMPLETED'
assert round(bot.haversine_m(59.9386, 30.3141, 59.9386, 30.3141)) == 0
print('SELFTEST_OK')
