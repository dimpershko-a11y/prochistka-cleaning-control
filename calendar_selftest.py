import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from calendar_sync import apply_events, parse_events

ICS = b'''BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:test-clean-1\r\nDTSTART:20260923T070000Z\r\nDTEND:20260923T110000Z\r\nSUMMARY:\xd0\x93\xd0\xb5\xd0\xbd\xd0\xb5\xd1\x80\xd0\xb0\xd0\xbb\xd1\x8c\xd0\xbd\xd0\xb0\xd1\x8f \xd1\x83\xd0\xb1\xd0\xbe\xd1\x80\xd0\xba\xd0\xb0\r\nLOCATION:\xd0\xa2\xd0\xb5\xd1\x81\xd1\x82\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x8f 1\r\nDESCRIPTION:\xd0\x98\xd0\xb2\xd0\xb0\xd0\xbd +79990000000\r\nSTATUS:CONFIRMED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''

def create_base(path):
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE orders(id INTEGER PRIMARY KEY AUTOINCREMENT,
        public_number TEXT UNIQUE,address TEXT NOT NULL,scheduled_date TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PLANNED',consent TEXT NOT NULL DEFAULT 'UNKNOWN',
        arrived_at TEXT,started_at TEXT,finished_at TEXT,created_at TEXT NOT NULL)''')
    conn.commit(); conn.close()
with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / 'test.db'
    create_base(db)
    events = parse_events(ICS)
    assert len(events) == 1
    assert events[0]['summary'] == 'Генеральная уборка'
    now = datetime(2026, 9, 22, 12, 0, tzinfo=ZoneInfo('Europe/Moscow'))
    result = apply_events(db, events, now=now)
    assert result['new'] == 1

    conn = sqlite3.connect(db)
    row = conn.execute('SELECT calendar_uid,status,address FROM orders').fetchone()
    assert row == ('test-clean-1', 'PLANNED', 'Тестовая 1')
    conn.close()

    result = apply_events(db, [], now=now)
    assert result['cancelled'] == 1
    conn = sqlite3.connect(db)
    status = conn.execute('SELECT status FROM orders').fetchone()[0]
    conn.close()
    assert status == 'CANCELLED'

print('CALENDAR_SELFTEST_OK')
