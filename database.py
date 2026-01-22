import sqlite3
from datetime import datetime
import os

DB_NAME = "zuppa.db"

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            remind_time TIMESTAMP NOT NULL,
            event_time TIMESTAMP,
            target_user TEXT NOT NULL,
            sent_status INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def add_reminder(message: str, remind_time: datetime, event_time: datetime, target_user: str):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO reminders (message, remind_time, event_time, target_user, sent_status) VALUES (?, ?, ?, ?, ?)',
              (message, remind_time, event_time, target_user, 0))
    conn.commit()
    reminder_id = c.lastrowid
    conn.close()
    return reminder_id

def get_pending_reminders():
    conn = get_db_connection()
    c = conn.cursor()
    # Get reminders that are due and haven't been sent (status 0)
    # We'll actually rely on the scheduler to pick them up, or polling.
    # For now, let's just return all pending for debugging or polling.
    c.execute('SELECT * FROM reminders WHERE sent_status = 0 ORDER BY remind_time ASC')
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def mark_reminder_sent(reminder_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE reminders SET sent_status = 1 WHERE id = ?', (reminder_id,))
    conn.commit()
    conn.close()
