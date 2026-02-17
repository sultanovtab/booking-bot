import os
import re
import threading
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn

from config import SETTINGS, get_admin_ids, QUESTS, is_compatible
import db as booking_db
from texts import quest_info_text, ADULT_RULES, KIDS_RULES, FINAL_WISH


# ---- FastAPI health ----
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok"}

def run_web():
    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


# ---- ENV ----
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в Render Environment.")

ADMIN_IDS = get_admin_ids()
TZ = ZoneInfo(SETTINGS.TZ)

PHONE_RE = re.compile(r"^\+?\d[\d \-\(\)]{8,20}\d$")

def normalize_phone(s: str) -> str:
    return re.sub(r"[ \-\(\)]", "", s.strip())

def phone_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def main_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📅 Забронировать", callback_data="action:book")
    kb.button(text="ℹ️ Что умеет бот", callback_data="action:help")
    kb.adjust(1)
    return kb.as_markup()

def category_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🔞 Взрослые квесты (14+)", callback_data="cat:adult")
    kb.button(text="🧒 Детские квесты (10–13)", callback_data="cat:kids")
    kb.adjust(1)
    return kb.as_markup()

def services_kb(category: str):
    kb = InlineKeyboardBuilder()
    for key, q in QUESTS.items():
        if q["category"] == category:
            kb.button(text=q["title"], callback_data=f"service:{key}")
    kb.adjust(1)
    kb.button(text="⬅️ Назад", callback_data="back:cats")
    kb.adjust(1, 1)
    return kb.as_markup()

def team_size_kb(max_team: int):
    kb = InlineKeyboardBuilder()
    for n in range(2, max_team + 1):
        kb.button(text=str(n), callback_data=f"team:{n}")
    kb.adjust(5)
    kb.button(text="⬅️ Назад", callback_data="back:services")
    kb.adjust(5, 1)
    return kb.as_markup()

def dates_kb():
    kb = InlineKeyboardBuilder()
    today = datetime.now(TZ).date()
    for i in range(0, SETTINGS.DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        kb.button(text=d.strftime("%d.%m"), callback_data=f"date:{d.isoformat()}")
    kb.adjust(3)
    kb.button(text="⬅️ Назад", callback_data="back:team")
    kb.adjust(3, 1)
    return kb.as_markup()

def generate_slots_for_date(d: date):
    start = datetime(d.year, d.month, d.day, SETTINGS.START_TIME.hour, SETTINGS.START_TIME.minute, tzinfo=TZ)
    end = datetime(d.year, d.month, d.day, SETTINGS.END_TIME.hour, SETTINGS.END_TIME.minute, tzinfo=TZ)
    step = timedelta(minutes=SETTINGS.SLOT_MINUTES)
    out = []
    t = start
    while t <= end:
        out.append(t)
        t += step
    return out

def slot_allowed_by_time(service_key: str, slot_dt: datetime) -> bool:
    q = QUESTS[service_key]
    # общий лимит по квесту
    if slot_dt.time() > q["last_start"]:
        return False

    # после 22:00 только Каннибал
    if slot_dt.time() >= SETTINGS.NIGHT_FROM:
        return service_key == "cannibal"
    return True

def slot_available_for_service(service_key: str, slot_iso: str, slot_dt: datetime) -> bool:
    # правило времени
    if not slot_allowed_by_time(service_key, slot_dt):
        return False

    existing = booking_db.list_slot_services(slot_iso)

    # после 22:00 — только 1 бронь на слот и только Каннибал
    if slot_dt.time() >= SETTINGS.NIGHT_FROM:
        if service_key != "cannibal":
            return False
        return len(existing) == 0  # запрещаем второй Каннибал

    # до 22:00 — максимум 2, но совместимость по правилам
    return is_compatible(service_key, existing)

def times_kb_for_date(d: date, service_key: str):
    kb = InlineKeyboardBuilder()
    for slot_dt in generate_slots_for_date(d):
        slot_iso = slot_dt.strftime("%Y-%m-%dT%H:%M")
        if slot_available_for_service(service_key, slot_iso, slot_dt):
            kb.button(text=slot_dt.strftime("%H:%M"), callback_data=f"slot:{slot_iso}")
    kb.adjust(4)
    kb.button(text="⬅️ Назад к датам", callback_data="back:dates")
    kb.adjust(4, 1)
    return kb.as_markup()

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

def calc_price(service_key: str, team_size: int, slot_dt: datetime) -> int:
    # детские
    if QUESTS[service_key]["category"] == "kids":
        base = SETTINGS.KIDS_2_4
        if team_size > 4:
            base += (team_size - 4) * SETTINGS.KIDS_ADD_PER_PERSON
        return base

    # взрослые (только 2–6)
    if 2 <= team_size <= 4:
        base = SETTINGS.ADULT_2_4
    elif team_size == 5:
        base = SETTINGS.ADULT_5
    else:
        base = SETTINGS.ADULT_6

    # ночная доплата: только для Каннибал в 22:00/23:30 (и она одна)
    if service_key == "cannibal" and slot_dt.time() >= SETTINGS.NIGHT_FROM:
        base += SETTINGS.NIGHT_EXTRA
    return base


class BookingFlow(StatesGroup):
    waiting_name = State()
    waiting_category = State()
    waiting_service = State()
    waiting_team = State()
    waiting_date = State()
    waiting_time = State()
    waiting_phone = State()


async def start(message: Message):
    await message.answer("Привет! Я бот для бронирования квестов.", reply_markup=main_menu_kb())

async def cmd_book(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(BookingFlow.waiting_name)
    await message.answer("Как вас зовут? (только буквы/пробел/дефис)")

async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=main_menu_kb())

def is_valid_name(text: str) -> bool:
    text = text.strip()
    return 2 <= len(text) <= 60 and bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё\- ]+", text))

async def got_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not is_valid_name(name):
        await message.answer("Имя выглядит странно 😅 Напишите буквами (можно пробел/дефис).")
        return
    await state.update_data(name=name)
    await state.set_state(BookingFlow.waiting_category)
    await message.answer("Выберите категорию:", reply_markup=category_kb())

async def choose_category(call: CallbackQuery, state: FSMContext):
    await call.answer()
    cat = (call.data or "").split("cat:", 1)[-1]
    if cat not in ("adult", "kids"):
        return
    await state.update_data(category=cat)
    await state.set_state(BookingFlow.waiting_service)
    await call.message.edit_text("Выберите квест:", reply_markup=services_kb(cat))

async def back_to_cats(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BookingFlow.waiting_category)
    await call.message.edit_text("Выберите категорию:", reply_markup=category_kb())

async def choose_service(call: CallbackQuery, state: FSMContext):
    await call.answer()
    key = (call.data or "").split("service:", 1)[-1].strip()
    if key not in QUESTS:
        return
    q = QUESTS[key]
    await state.update_data(service_key=key, service_title=q["title"], max_team=q["max_team"])
    await state.set_state(BookingFlow.waiting_team)
    await call.message.edit_text("Сколько человек в команде?", reply_markup=team_size_kb(q["max_team"]))

async def back_to_services(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    cat = data.get("category", "adult")
    await state.set_state(BookingFlow.waiting_service)
    await call.message.edit_text("Выберите квест:", reply_markup=services_kb(cat))

async def choose_team(call: CallbackQuery, state: FSMContext):
    await call.answer()
    n = int((call.data or "").split("team:", 1)[-1])
    data = await state.get_data()
    max_team = int(data["max_team"])
    if n < 2 or n > max_team:
        return
    await state.update_data(team_size=n)
    await state.set_state(BookingFlow.waiting_date)
    await call.message.edit_text("Выберите дату:", reply_markup=dates_kb())

async def back_to_team(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    max_team = int(data.get("max_team", 6))
    await state.set_state(BookingFlow.waiting_team)
    await call.message.edit_text("Сколько человек в команде?", reply_markup=team_size_kb(max_team))

async def choose_date(call: CallbackQuery, state: FSMContext):
    await call.answer()
    d_str = (call.data or "").split("date:", 1)[-1]
    d = date.fromisoformat(d_str)
    data = await state.get_data()
    service_key = data["service_key"]
    await state.update_data(date_iso=d.isoformat())
    await state.set_state(BookingFlow.waiting_time)
    await call.message.edit_text(
        f"Выберите время на {d.strftime('%d.%m.%Y')}:",
        reply_markup=times_kb_for_date(d, service_key)
    )

async def back_to_dates(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BookingFlow.waiting_date)
    await call.message.edit_text("Выберите дату:", reply_markup=dates_kb())

async def choose_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    slot_iso = (call.data or "").split("slot:", 1)[-1]
    slot_dt = datetime.strptime(slot_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)

    data = await state.get_data()
    service_key = data["service_key"]

    if not slot_available_for_service(service_key, slot_iso, slot_dt):
        d = date.fromisoformat(data["date_iso"])
        await call.message.answer("Это время недоступно. Выберите другое.")
        await call.message.answer("Доступные времена:", reply_markup=times_kb_for_date(d, service_key))
        return

    await state.update_data(slot_iso=slot_iso)

    # ночное предупреждение перед телефоном (только Каннибал 22:00/23:30)
    if service_key == "cannibal" and slot_dt.time() >= SETTINGS.NIGHT_FROM:
        await call.message.answer("⚠️ Доплата +1000 рублей за бронирование в ночное время.")

    await state.set_state(BookingFlow.waiting_phone)
    await call.message.answer(
        "Отправьте номер телефона:\n• кнопкой «Поделиться контактом»\n• или напишите вручную (+79991234567)",
        reply_markup=phone_kb()
    )

async def got_phone(message: Message, state: FSMContext, bot: Bot):
    phone = None
    if message.contact and message.contact.phone_number:
        phone = message.contact.phone_number
    else:
        txt = (message.text or "").strip()
        if PHONE_RE.fullmatch(txt):
            phone = txt
    if not phone:
        await message.answer("Не вижу корректный номер. Отправьте контакт кнопкой или введите номер вручную.")
        return
    phone = normalize_phone(phone)

    data = await state.get_data()
    name = data["name"]
    service_key = data["service_key"]
    service_title = data["service_title"]
    team_size = int(data["team_size"])
    slot_iso = data["slot_iso"]
    slot_dt = datetime.strptime(slot_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)

    # повторная проверка слота
    if not slot_available_for_service(service_key, slot_iso, slot_dt):
        d = date.fromisoformat(data["date_iso"])
        await state.set_state(BookingFlow.waiting_time)
        await message.answer("Похоже, это время стало недоступно. Выберите другое:")
        await message.answer("Доступные времена:", reply_markup=times_kb_for_date(d, service_key))
        return

    booking_id = booking_db.create_booking(
        tg_user_id=message.from_user.id,
        tg_username=message.from_user.username,
        name=name,
        phone=phone,
        service_key=service_key,
        service_title=service_title,
        team_size=team_size,
        slot_iso=slot_iso
    )

    await message.answer(
        f"✅ Заявка отправлена!\nНомер: #{booking_id}\nОжидайте подтверждения администратора.",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer("Главное меню:", reply_markup=main_menu_kb())

    user_link = f"@{message.from_user.username}" if message.from_user.username else "(без username)"
    slot_str = slot_dt.strftime("%d.%m.%Y %H:%M")

    admin_text = (
        f"📌 Новая бронь #{booking_id}\n\n"
        f"Квест: {service_title}\n"
        f"Дата/время: {slot_str}\n"
        f"Команда: {team_size}\n"
        f"Телефон: {phone}\n\n"
        f"Пользователь: {user_link} | user_id={message.from_user.id}"
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, admin_text, reply_markup=admin_confirm_kb(booking_id))
        except Exception:
            pass

    await state.clear()

def admin_display_name(u) -> str:
    return f"@{u.username}" if u.username else u.full_name

async def admin_confirm(call: CallbackQuery, bot: Bot):
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

    # 1) подтверждение + цена/адрес
    await bot.send_message(
        tg_user_id,
        f"Ждем вас {slot_str} на квесте «{service_title}».\n"
        f"Цена за {team_size} человек будет {price} рублей.\n"
        f"{SETTINGS.PAYMENT}\n"
        f"Находимся мы по адресу {SETTINGS.ADDRESS}"
    )

    # Взрослые: 2-е сообщение + правила + кнопка
    if QUESTS[service_key]["has_info"]:
        await bot.send_message(tg_user_id, quest_info_text(service_key))
        await bot.send_message(tg_user_id, ADULT_RULES, reply_markup=rules_ack_kb(booking_id))
    else:
        # Детские: только правила (в них уже есть “Желаем…”, без кнопки)
        await bot.send_message(tg_user_id, KIDS_RULES)

    # уведомление админам кто подтвердил
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"✅ Бронь #{booking_id} подтверждена.\nПодтвердил: {admin_name}")
        except Exception:
            pass

    await call.message.answer(f"Подтверждено: #{booking_id}")

async def admin_reject(call: CallbackQuery, bot: Bot):
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

def is_admin(user_id: int) -> bool:
    return user_id in set(ADMIN_IDS)

async def admin_menu(message: Message):
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
        lines.append(f"#{bid} | {t} | {title} | {team} чел | {status} | подтвердил: {conf}")

    text = "\n".join(lines)
    # телега режет длинные сообщения — на всякий
    for chunk_start in range(0, len(text), 3500):
        await call.message.answer(text[chunk_start:chunk_start+3500])

async def action_buttons(call: CallbackQuery, state: FSMContext):
    if call.data == "action:help":
        await call.answer()
        await call.message.edit_text(
            "• /start — меню\n• /book — бронь\n• /cancel — отмена\n• /admin — список броней (только админы)\n\n"
            "Взрослые квесты: 10:00–20:30, Каннибал до 23:30.\n"
            "После 22:00 — только Каннибал (и только одна бронь на слот).",
            reply_markup=main_menu_kb()
        )
        return

    if call.data == "action:book":
        await call.answer()
        await state.clear()
        await state.set_state(BookingFlow.waiting_name)
        await call.message.edit_text("Как вас зовут? (только буквы/пробел/дефис)")
        return

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start, CommandStart())
    dp.message.register(cmd_book, Command("book"))
    dp.message.register(cancel, Command("cancel"))
    dp.message.register(admin_menu, Command("admin"))

    dp.callback_query.register(action_buttons, F.data.in_({"action:book", "action:help"}))

    dp.message.register(got_name, BookingFlow.waiting_name)

    dp.callback_query.register(choose_category, F.data.startswith("cat:"), BookingFlow.waiting_category)
    dp.callback_query.register(back_to_cats, F.data == "back:cats", BookingFlow.waiting_service)

    dp.callback_query.register(choose_service, F.data.startswith("service:"), BookingFlow.waiting_service)
    dp.callback_query.register(back_to_services, F.data == "back:services", BookingFlow.waiting_team)

    dp.callback_query.register(choose_team, F.data.startswith("team:"), BookingFlow.waiting_team)
    dp.callback_query.register(back_to_team, F.data == "back:team", BookingFlow.waiting_date)

    dp.callback_query.register(choose_date, F.data.startswith("date:"), BookingFlow.waiting_date)
    dp.callback_query.register(back_to_dates, F.data == "back:dates", BookingFlow.waiting_time)

    dp.callback_query.register(choose_time, F.data.startswith("slot:"), BookingFlow.waiting_time)
    dp.message.register(got_phone, BookingFlow.waiting_phone)

    dp.callback_query.register(admin_confirm, F.data.startswith("admin:confirm:"))
    dp.callback_query.register(admin_reject, F.data.startswith("admin:reject:"))

    dp.callback_query.register(rules_ok, F.data.startswith("rules_ok:"))

    dp.callback_query.register(admin_choose_date, F.data.startswith("admin_date:"))

    return dp

async def main():
    booking_db.init_db()
    threading.Thread(target=run_web, daemon=True).start()

    bot = Bot(token=BOT_TOKEN)
    dp = build_dispatcher()
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
