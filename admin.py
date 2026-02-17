# admin.py
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, F
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db as booking_db
from config import SETTINGS, get_admin_ids, QUESTS
from booking_logic import calc_price
from texts import quest_info_text, ADULT_RULES, KIDS_RULES, FINAL_WISH


ADMIN_IDS = set(get_admin_ids())
TZ = ZoneInfo(SETTINGS.TZ)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_confirm_kb(booking_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=f"admin:confirm:{booking_id}")
    kb.button(text="❌ Отклонить", callback_data=f"admin:reject:{booking_id}")
    kb.adjust(2)
    return kb.as_markup()


def rules_ack_kb(booking_id: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="Я ознакомлен(а) с правилами✅", callback_data=f"rules_ok:{booking_id}")
    kb.adjust(1)
    return kb.as_markup()


def admin_dates_kb():
    kb = InlineKeyboardBuilder()
    today = datetime.now(TZ).date()
    for i in range(0, SETTINGS.DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        kb.button(text=d.strftime("%d.%m"), callback_data=f"admin_date:{d.isoformat()}")
    kb.adjust(4)
    return kb.as_markup()


def admin_display_name(u) -> str:
    return f"@{u.username}" if u.username else u.full_name


async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Выберите дату для просмотра броней:", reply_markup=admin_dates_kb())


async def admin_choose_date(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer()
        return

    await call.answer()
    d_iso = (call.data or "").split("admin_date:", 1)[-1]

    rows = booking_db.list_bookings_for_date(d_iso)
    if not rows:
        await call.message.answer(f"На {d_iso} броней нет.")
        return

    lines = [f"Брони на {d_iso}:\n"]
    for (bid, title, team, name, phone, slot_iso, status, confirmed_by) in rows:
        t = slot_iso.split("T")[1]
        conf = confirmed_by or "-"
        lines.append(f"#{bid} | {t} | {title} | {team} чел | {status} | подтвердил: {conf} | {name} | {phone}")

    text = "\n".join(lines)
    for i in range(0, len(text), 3500):
        await call.message.answer(text[i:i+3500])


async def admin_confirm(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer()
        return

    await call.answer()
    booking_id = int((call.data or "").split(":")[-1])
    admin_name = admin_display_name(call.from_user)

    changed = booking_db.confirm_booking(booking_id, call.from_user.id, admin_name)
    if changed == 0:
        await call.message.answer("Эта бронь уже обработана.")
        return

    row = booking_db.get_booking(booking_id)
    if not row:
        await call.message.answer("Не нашёл бронь в базе.")
        return

    (_id, tg_user_id, tg_username, client_name, phone, service_key, service_title,
     team_size, slot_iso, status, c_by_id, c_by_name, c_at) = row

    slot_dt = datetime.strptime(slot_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
    slot_str = slot_dt.strftime("%d.%m.%Y %H:%M")

    price = calc_price(service_key, team_size, slot_dt)

    # 1) сообщение подтверждения
    await bot.send_message(
        tg_user_id,
        f"Ждем вас {slot_str} на квесте «{service_title}».\n"
        f"Цена за {team_size} человек будет {price} рублей.\n"
        f"{SETTINGS.PAYMENT}\n"
        f"Находимся мы по адресу {SETTINGS.ADDRESS}"
    )

    # 2) доп сообщение только для взрослых квестов (inferno/patient0/cannibal)
    if QUESTS[service_key]["has_info"]:
        await bot.send_message(tg_user_id, quest_info_text(service_key))
        # 3) правила + кнопка
        await bot.send_message(tg_user_id, ADULT_RULES, reply_markup=rules_ack_kb(booking_id))
    else:
        # детские: сразу правила (внутри текста уже есть пожелание)
        await bot.send_message(tg_user_id, KIDS_RULES)

    # уведомление админам кто подтвердил
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"✅ Бронь #{booking_id} подтверждена.\nПодтвердил: {admin_name}")
        except Exception:
            pass

    await call.message.answer(f"Подтверждено: #{booking_id}")


async def admin_reject(call: CallbackQuery, bot: Bot):
    if not is_admin(call.from_user.id):
        await call.answer()
        return

    await call.answer()
    booking_id = int((call.data or "").split(":")[-1])
    admin_name = admin_display_name(call.from_user)

    changed = booking_db.reject_booking(booking_id)
    if changed == 0:
        await call.message.answer("Эта бронь уже обработана.")
        return

    row = booking_db.get_booking(booking_id)
    if row:
        tg_user_id = row[1]
        await bot.send_message(tg_user_id, "К сожалению, время недоступно. Создайте бронь заново: /start")

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"❌ Бронь #{booking_id} отклонена.\nОтклонил: {admin_name}")
        except Exception:
            pass

    await call.message.answer(f"Отклонено: #{booking_id}")


async def rules_ok(call: CallbackQuery, bot: Bot):
    await call.answer()
    await bot.send_message(call.from_user.id, FINAL_WISH)
