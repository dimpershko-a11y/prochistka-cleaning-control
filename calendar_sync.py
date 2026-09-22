from __future__ import annotations

import sqlite3
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Calendar

ACTIVE_PRESTART = {
    'PLANNED', 'ARRIVED', 'BEFORE_REQUIRED', 'READY_TO_START'
}


def _as_text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()


def _as_datetime(value, tz: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value.astimezone(tz)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=tz)
    raise ValueError(f'Unsupported calendar date: {value!r}')


def fetch_ical(url: str, timeout: int = 20) -> bytes:
    request = urllib.request.Request(
        url,
        headers={'User-Agent': 'PRO-CHISTKA-Control/1.0'},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    if b'BEGIN:VCALENDAR' not in data:
        raise RuntimeError('Google Calendar returned invalid iCal data')
    return data


def parse_events(
    raw: bytes,
    timezone_name: str = 'Europe/Moscow',
    keywords: tuple[str, ...] = ('клининг', 'уборка'),
) -> list[dict]:
    tz = ZoneInfo(timezone_name)
    calendar = Calendar.from_ical(raw)
    events: list[dict] = []
    lowered = tuple(k.lower() for k in keywords if k.strip())

    for component in calendar.walk('VEVENT'):
        summary = _as_text(component.get('SUMMARY'))
        if lowered and not any(k in summary.lower() for k in lowered):
            continue
        uid = _as_text(component.get('UID'))
        if not uid:
            continue
        start = _as_datetime(component.decoded('DTSTART'), tz)
        end_value = component.decoded('DTEND') if component.get('DTEND') else start
        end = _as_datetime(end_value, tz)
        modified = component.get('LAST-MODIFIED')
        modified_iso = ''
        if modified is not None:
            modified_iso = _as_datetime(modified.dt, tz).isoformat(timespec='seconds')

        events.append({
            'uid': uid,
            'summary': summary,
            'location': _as_text(component.get('LOCATION')),
            'description': _as_text(component.get('DESCRIPTION')),
            'start': start.isoformat(timespec='seconds'),
            'end': end.isoformat(timespec='seconds'),
            'status': _as_text(component.get('STATUS')).upper() or 'CONFIRMED',
            'last_modified': modified_iso,
            'sequence': int(component.get('SEQUENCE', 0) or 0),
        })
    return events


def ensure_schema(db_path: str | Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(orders)')}
        migrations = {
            'calendar_uid': 'TEXT',
            'source': "TEXT NOT NULL DEFAULT 'manual'",
            'title': 'TEXT',
            'description': 'TEXT',
            'scheduled_start': 'TEXT',
            'scheduled_end': 'TEXT',
            'calendar_last_modified': 'TEXT',
            'calendar_sequence': 'INTEGER DEFAULT 0',
        }
        for name, sql_type in migrations.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE orders ADD COLUMN {name} {sql_type}')
        conn.execute(
            'CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_calendar_uid '
            'ON orders(calendar_uid) WHERE calendar_uid IS NOT NULL'
        )
        conn.commit()
    finally:
        conn.close()


def _in_window(start_iso: str, now: datetime) -> bool:
    start = datetime.fromisoformat(start_iso)
    return now.date() <= start.date() <= (now + timedelta(days=180)).date()


def apply_events(
    db_path: str | Path,
    events: list[dict],
    timezone_name: str = 'Europe/Moscow',
    now: datetime | None = None,
) -> dict:
    tz = ZoneInfo(timezone_name)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    ensure_schema(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    result = {'new': 0, 'updated': 0, 'cancelled': 0, 'seen': 0}
    seen: set[str] = set()

    try:
        for event in events:
            if not _in_window(event['start'], current):
                continue
            uid = event['uid']
            seen.add(uid)
            result['seen'] += 1
            row = conn.execute(
                'SELECT * FROM orders WHERE calendar_uid=?', (uid,)
            ).fetchone()

            if event['status'] == 'CANCELLED':
                if row and row['status'] in ACTIVE_PRESTART:
                    conn.execute(
                        "UPDATE orders SET status='CANCELLED' WHERE id=?", (row['id'],)
                    )
                    result['cancelled'] += 1
                continue

            start = datetime.fromisoformat(event['start'])
            address = event['location'] or 'Адрес не указан'
            values = (
                address,
                start.date().isoformat(),
                event['summary'],
                event['description'],
                event['start'],
                event['end'],
                event['last_modified'],
                event['sequence'],
            )
            if row is None:
                cur = conn.execute(
                    "INSERT INTO orders(address,scheduled_date,status,consent,created_at,"
                    "calendar_uid,source,title,description,scheduled_start,scheduled_end,"
                    "calendar_last_modified,calendar_sequence) "
                    "VALUES(?,?,'PLANNED','UNKNOWN',datetime('now'),?,'calendar',?,?,?,?,?,?)",
                    (address, start.date().isoformat(), uid, event['summary'],
                     event['description'], event['start'], event['end'],
                     event['last_modified'], event['sequence']),
                )
                oid = cur.lastrowid
                public_number = f"G{start.strftime('%m%d')}-{oid}"
                conn.execute(
                    'UPDATE orders SET public_number=? WHERE id=?',
                    (public_number, oid),
                )
                result['new'] += 1
                continue

            changed = any([
                row['address'] != address,
                row['scheduled_date'] != start.date().isoformat(),
                row['title'] != event['summary'],
                row['description'] != event['description'],
                row['scheduled_start'] != event['start'],
                row['scheduled_end'] != event['end'],
            ])
            if changed and row['status'] != 'COMPLETED':
                conn.execute(
                    'UPDATE orders SET address=?,scheduled_date=?,title=?,description=?, '
                    'scheduled_start=?,scheduled_end=?,calendar_last_modified=?, '
                    'calendar_sequence=? WHERE id=?',
                    values + (row['id'],),
                )
                result['updated'] += 1

        rows = conn.execute(
            "SELECT id,calendar_uid,status,scheduled_start FROM orders "
            "WHERE source='calendar' AND calendar_uid IS NOT NULL"
        ).fetchall()
        for row in rows:
            if row['status'] not in ACTIVE_PRESTART or not row['scheduled_start']:
                continue
            start = datetime.fromisoformat(row['scheduled_start'])
            if current.date() <= start.date() <= (current + timedelta(days=180)).date():
                if row['calendar_uid'] not in seen:
                    conn.execute(
                        "UPDATE orders SET status='CANCELLED' WHERE id=?", (row['id'],)
                    )
                    result['cancelled'] += 1
        conn.commit()
        return result
    finally:
        conn.close()


def sync_calendar(
    db_path: str | Path,
    ical_url: str,
    timezone_name: str = 'Europe/Moscow',
    keywords: tuple[str, ...] = ('клининг', 'уборка'),
) -> dict:
    if not ical_url:
        raise RuntimeError('Google Calendar is not connected')
    raw = fetch_ical(ical_url)
    events = parse_events(raw, timezone_name, keywords)
    return apply_events(db_path, events, timezone_name)
