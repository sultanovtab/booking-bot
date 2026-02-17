# db.py
import sqlite3
from datetime import datetime

DB_PATH = "bookings.sqlite3"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        tg_user_id INTEGER NOT NULL,
        tg_username TEXT,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        service_key TEXT NOT NULL,
        service_title TEXT NOT NULL,
        team_size INTEGER NOT NULL,
        slot_iso TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',

        confirmed_by_id INTEGER,
        confirmed_by_name TEXT,
        confirmed_at TEXT
    )
    """)
    con.commit()
    con.close()


def is_slot_taken(slot_iso: str) -> bool:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT 1 FROM bookings WHERE slot_iso=? AND status IN ('pending','confirmed') LIMIT 1",
        (slot_iso,)
    )
    row = cur.fetchone()
    con.close()
    return row is not None


def create_booking(*, tg_user_id: int, tg_username: str | None, name: str, phone: str,
                   service_key: str, service_title: str, team_size: int, slot_iso: str) -> int:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO bookings (
            created_at,
            tg_user_id,
            tg_username,
            name,
            phone,
            service_key,
            service_title,
            team_size,
            slot_iso,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
    """, (
        datetime.utcnow().isoformat(timespec="seconds"),
        tg_user_id,
        tg_username,
        name,
        phone,
        service_key,
        service_title,
        team_size,
        slot_iso
    ))
    con.commit()
    booking_id = cur.lastrowid
    con.close()
    return booking_id


def confirm_booking(booking_id: int, admin_id: int, admin_name: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        UPDATE bookings
        SET status='confirmed',
            confirmed_by_id=?,
            confirmed_by_name=?,
            confirmed_at=?
        WHERE id=? AND status='pending'
    """, (
        admin_id,
        admin_name,
        datetime.utcnow().isoformat(timespec="seconds"),
        booking_id
    ))
    con.commit()
    con.close()


def set_status(booking_id: int, status: str):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "UPDATE bookings SET status=? WHERE id=?",
        (status, booking_id)
    )
    con.commit()
    con.close()


def get_booking(booking_id: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
      SELECT
        id,
        tg_user_id,
        tg_username,
        name,
        phone,
        service_key,
        service_title,
        team_size,
        slot_iso,
        status,
        confirmed_by_id,
        confirmed_by_name,
        confirmed_at
      FROM bookings
      WHERE id=?
    """, (booking_id,))
    row = cur.fetchone()
    con.close()
    return row
