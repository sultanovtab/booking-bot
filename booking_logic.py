# booking_logic.py
from __future__ import annotations

from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from config import SETTINGS, QUESTS, is_compatible
import db as booking_db


def tz():
    return ZoneInfo(SETTINGS.TZ)


def generate_slots_for_date(d: date) -> list[datetime]:
    Z = tz()
    start = datetime(d.year, d.month, d.day, SETTINGS.START_TIME.hour, SETTINGS.START_TIME.minute, tzinfo=Z)
    end = datetime(d.year, d.month, d.day, SETTINGS.END_TIME.hour, SETTINGS.END_TIME.minute, tzinfo=Z)
    step = timedelta(minutes=SETTINGS.SLOT_MINUTES)

    out: list[datetime] = []
    t = start
    while t <= end:
        out.append(t)
        t += step
    return out


def slot_allowed_by_time(service_key: str, slot_dt: datetime) -> bool:
    q = QUESTS[service_key]

    # общий лимит по квесту (например 20:30 для всех кроме каннибала)
    if slot_dt.time() > q["last_start"]:
        return False

    # после 22:00 — только Каннибал
    if slot_dt.time() >= SETTINGS.NIGHT_FROM:
        return service_key == "cannibal"

    return True


def slot_available_for_service(service_key: str, slot_iso: str, slot_dt: datetime) -> bool:
    # правило времени
    if not slot_allowed_by_time(service_key, slot_dt):
        return False

    existing = booking_db.list_slot_services(slot_iso)

    # после 22:00 — только один Каннибал (без параллелей)
    if slot_dt.time() >= SETTINGS.NIGHT_FROM:
        if service_key != "cannibal":
            return False
        return len(existing) == 0

    # до 22:00 — максимум 2 и совместимость (каннибал + любой один другой)
    return is_compatible(service_key, existing)


def calc_price(service_key: str, team_size: int, slot_dt: datetime) -> int:
    # детские
    if QUESTS[service_key]["category"] == "kids":
        base = SETTINGS.KIDS_2_4
        if team_size > 4:
            base += (team_size - 4) * SETTINGS.KIDS_ADD_PER_PERSON
        return base

    # взрослые (2–6)
    if 2 <= team_size <= 4:
        base = SETTINGS.ADULT_2_4
    elif team_size == 5:
        base = SETTINGS.ADULT_5
    else:
        base = SETTINGS.ADULT_6

    # ночная доплата только одна (+1000) и только после 22:00
    if service_key == "cannibal" and slot_dt.time() >= SETTINGS.NIGHT_FROM:
        base += SETTINGS.NIGHT_EXTRA

    return base


def is_night_slot(service_key: str, slot_dt: datetime) -> bool:
    return service_key == "cannibal" and slot_dt.time() >= SETTINGS.NIGHT_FROM
