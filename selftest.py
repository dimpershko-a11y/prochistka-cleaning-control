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

order = bot.create_order('Санкт-Петербург, тестовый адрес 1')
assert order['status'] == 'PLANNED'
bot.set_status(order['id'], 'ARRIVED')
bot.set_status(order['id'], 'READY_TO_START', 'consent_denied')
bot.set_status(order['id'], 'IN_PROGRESS')
bot.set_status(order['id'], 'READY_TO_COMPLETE')
bot.set_status(order['id'], 'COMPLETED')
assert bot.get_order(order['id'])['status'] == 'COMPLETED'

with bot.db() as c:
    n = c.execute('SELECT COUNT(*) n FROM status_history WHERE order_id=?',
                  (order['id'],)).fetchone()['n']
assert n == 5

print('SELFTEST_OK')
