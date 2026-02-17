import os
import re
import threading
from datetime import datetime, timedelta, date, time as dtime
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

from config import SETTINGS, get_admin_ids
import db as booking_db
from texts import quest_info_text, RULES_TEXT, FINAL_WISH


# --- FastAPI health (Render web-service needs a port) ---
app = FastAPI()

@app.get("/")
def root():
    return {"status": "ok"}

def run_web():
    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)


# --- ENV ---
load_dotenv()  # локально может читать .env, на Render — ENV variables

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в переменных окружения Render (Environment).")

ADMIN_IDS = get_admin_ids()

TZ = ZoneInfo(SETTINGS.TZ)

SERVICES = [
    ("inferno", "Инферно"),
    ("patient0", "Нулевой пациент"),
    ("cannibal", "Каннибал"),
]

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

def services_kb():
    kb = InlineKeyboardBuilder()
    for key, title in SERVICES:
        kb.button(text=title, callback_data=f"service:{key}")
    kb.adjust(1)
    return kb.as_markup()

def team_size_kb():
    kb = InlineKeyboardBuilder()
    for n in range(2, 7):
        kb.button(text=str(n), callback_data=f"team:{n}")
    kb.adjust(5)
    return kb.as_markup()

def dates_kb():
    kb = InlineKeyboardBuilder()
    today = datetime.now(TZ).date()
    for i in range(0, SETTINGS.DAYS_AHEAD + 1):
        d = today + timedelta(days=i)
        text = d.strftime("%d.%m (%a)").replace("Mon","Пн").replace("Tue","Вт").replace("Wed","Ср").replace("Thu","Чт").replace("Fri","Пт").replace("Sat","Сб").replace("Sun","Вс")
        kb.button(text=text, callback_data=f"date:{d.isoformat()}")
    kb.adjust(2)
    return kb.as_markup()

def generate_slots_for_date(d: date) -> list[datetime]:
    start = datetime(d.year, d.month, d.day, SETTINGS.START_TIME.hour, SETTINGS.START_TIME.minute, tzinfo=TZ)
    end = datetime(d.year, d.month, d.day, SETTINGS.END_TIME.hour, SETTINGS.END_TIME.minute, tzinfo=TZ)
    step = timedelta(minutes=SETTINGS.SLOT_MINUTES)
    slots = []
    t = start
    while t <= end:
        slots.append(t)
        t += step
    return slots

def times_kb_for_date(d: date):
    kb = InlineKeyboardBuilder()
    for slot_dt in generate_slots_for_date(d):
        slot_iso = slot_dt.strftime("%Y-%m-%dT%H:%M")
        if booking_db.is_slot_taken(slot_iso):
            continue
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

def calc_price(team_size: int, slot_dt: datetime) -> int:
    if 2 <= team_size <= 4:
        base = SETTINGS.PRICE_2_4
    elif team_size == 5:
        base = SETTINGS.PRICE_5
    else:
        base = SETTINGS.PRICE_6

    if slot_dt.time() >= SETTINGS.AFTER_21_TIME:
        base += SETTINGS.AFTER_21_EXTRA
    return base


class BookingFlow(StatesGroup):
    waiting_name = State()
    waiting_service = State()
    waiting_team = State()
    waiting_date = State()
    waiting_time = State()
    waiting_phone = State()


async def start(message: Message):
    await message.answer(
        "Привет! Я бот для бронирования квестов.\n\nНажми «Забронировать», и я соберу заявку.",
        reply_markup=main_menu_kb(),
    )

async def help_text(call: CallbackQuery):
    await call.message.edit_text(
        "Команды:\n"
        "• /start — меню\n"
        "• /book — начать бронь\n"
        "• /cancel — отменить\n\n"
        "Бронь доступна только на ближайшие 12 дней. Время: 10:00–23:30, шаг 1,5 часа.",
        reply_markup=main_menu_kb(),
    )
    await call.answer()

async def cmd_book(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(BookingFlow.waiting_name)
    await message.answer("Как вас зовут? (только буквы/пробел/дефис)")

async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, отменил. Если нужно — нажми «Забронировать».", reply_markup=main_menu_kb())

def is_valid_name(text: str) -> bool:
    text = text.strip()
    if len(text) < 2 or len(text) > 60:
        return False
    return bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё\- ]+", text))

async def got_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if not is_valid_name(name):
        await message.answer("Имя выглядит странно 😅\nНапишите, пожалуйста, только буквами (можно пробел/дефис).")
        return

    await state.update_data(name=name)
    await state.set_state(BookingFlow.waiting_service)
    await message.answer("Выберите квест:", reply_markup=services_kb())

async def choose_service(call: CallbackQuery, state: FSMContext):
    await call.answer()
    key = (call.data or "").split("service:", 1)[-1].strip()
    title = next((t for k, t in SERVICES if k == key), None)
    if not title:
        await call.message.answer("Не понял квест. Начни заново: /book")
        return

    await state.update_data(service_key=key, service_title=title)
    await state.set_state(BookingFlow.waiting_team)
    await call.message.edit_text("Сколько человек в команде? (2–6)", reply_markup=team_size_kb())

async def choose_team(call: CallbackQuery, state: FSMContext):
    await call.answer()
    n = int((call.data or "").split("team:", 1)[-1])
    if n < 2 or n > 6:
        return

    await state.update_data(team_size=n)
    await state.set_state(BookingFlow.waiting_date)
    await call.message.edit_text("Выберите дату (только ближайшие 12 дней):", reply_markup=dates_kb())

async def choose_date(call: CallbackQuery, state: FSMContext):
    await call.answer()
    date_str = (call.data or "").split("date:", 1)[-1]
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return

    await state.update_data(date_iso=d.isoformat())
    await state.set_state(BookingFlow.waiting_time)
    await call.message.edit_text(
        f"Выберите время на {d.strftime('%d.%m.%Y')}:",
        reply_markup=times_kb_for_date(d)
    )

async def back_to_dates(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(BookingFlow.waiting_date)
    await call.message.edit_text("Выберите дату:", reply_markup=dates_kb())

async def choose_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    slot_iso = (call.data or "").split("slot:", 1)[-1]  # YYYY-MM-DDTHH:MM
    if booking_db.is_slot_taken(slot_iso):
        await call.message.answer("Это время уже занято. Выберите другое.")
        data = await state.get_data()
        d = date.fromisoformat(data["date_iso"])
        await call.message.answer("Доступные времена:", reply_markup=times_kb_for_date(d))
        return

    await state.update_data(slot_iso=slot_iso)
    await state.set_state(BookingFlow.waiting_phone)
    await call.message.answer(
        "Отправьте номер телефона:\n"
        "• нажмите кнопку «Поделиться контактом»\n"
        "• или напишите номер вручную (например +79991234567)",
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
    team_size = data["team_size"]
    slot_iso = data["slot_iso"]

    # финальная проверка слота
    if booking_db.is_slot_taken(slot_iso):
        await message.answer("Похоже, это время только что заняли. Выберите другое.")
        d = date.fromisoformat(data["date_iso"])
        await state.set_state(BookingFlow.waiting_time)
        await message.answer("Доступные времена:", reply_markup=times_kb_for_date(d))
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
        f"✅ Заявка отправлена!\n"
        f"Номер: #{booking_id}\n"
        f"Ожидайте подтверждения администратора.",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer("Главное меню:", reply_markup=main_menu_kb())

    # сообщение админам
    user_link = f"@{message.from_user.username}" if message.from_user.username else "(без username)"
    slot_dt = datetime.strptime(slot_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
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
    if u.username:
        return f"@{u.username}"
    return u.full_name


async def admin_confirm(call: CallbackQuery, bot: Bot):
    await call.answer()
    booking_id = int((call.data or "").split(":")[-1])
    name = admin_display_name(call.from_user)

    changed = booking_db.confirm_booking(booking_id, call.from_user.id, name)
    if changed == 0:
        await call.message.answer("Эта бронь уже обработана (подтверждена/отклонена).")
        return

    row = booking_db.get_booking(booking_id)
    if not row:
        await call.message.answer("Не нашёл бронь в базе.")
        return

    (_id, tg_user_id, tg_username, client_name, phone, service_key, service_title,
     team_size, slot_iso, status, c_by_id, c_by_name, c_at) = row

    slot_dt = datetime.strptime(slot_iso, "%Y-%m-%dT%H:%M").replace(tzinfo=TZ)
    slot_str = slot_dt.strftime("%d.%m.%Y %H:%M")
    price = calc_price(team_size, slot_dt)

    # 1) подтверждение + цена/адрес
    await bot.send_message(
        tg_user_id,
        f"Ждем вас {slot_str} на квесте «{service_title}».\n"
        f"Цена за {team_size} человек будет {price} рублей.\n"
        f"{SETTINGS.PAYMENT}\n"
        f"Находимся мы по адресу {SETTINGS.ADDRESS}"
    )

    # 2) инфо по квесту
    await bot.send_message(tg_user_id, quest_info_text(service_key))

    # 3) правила + кнопка ознакомления
    await bot.send_message(
        tg_user_id,
        RULES_TEXT,
        reply_markup=rules_ack_kb(booking_id)
    )

    # уведомление всем админам: кто подтвердил
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"✅ Бронь #{booking_id} подтверждена.\nПодтвердил: {name}"
            )
        except Exception:
            pass

    await call.message.answer(f"Готово. Вы подтвердили бронь #{booking_id}.")

async def admin_reject(call: CallbackQuery, bot: Bot):
    await call.answer()
    booking_id = int((call.data or "").split(":")[-1])
    name = admin_display_name(call.from_user)

    changed = booking_db.reject_booking(booking_id)
    if changed == 0:
        await call.message.answer("Эта бронь уже обработана (подтверждена/отклонена).")
        return

    row = booking_db.get_booking(booking_id)
    if row:
        tg_user_id = row[1]
        await bot.send_message(
            tg_user_id,
            "К сожалению, выбранное время недоступно. Пожалуйста, создайте бронь заново и выберите другое время.\n\n/start"
        )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"❌ Бронь #{booking_id} отклонена.\nОтклонил: {name}"
            )
        except Exception:
            pass

    await call.message.answer(f"Отклонено: бронь #{booking_id}.")


async def rules_ok(call: CallbackQuery, bot: Bot):
    await call.answer()
    await bot.send_message(call.from_user.id, FINAL_WISH)


async def action_buttons(call: CallbackQuery, state: FSMContext):
    if call.data == "action:help":
        await help_text(call)
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

    dp.callback_query.register(action_buttons, F.data.in_({"action:book", "action:help"}))

    dp.message.register(got_name, BookingFlow.waiting_name)
    dp.callback_query.register(choose_service, F.data.startswith("service:"), BookingFlow.waiting_service)
    dp.callback_query.register(choose_team, F.data.startswith("team:"), BookingFlow.waiting_team)

    dp.callback_query.register(choose_date, F.data.startswith("date:"), BookingFlow.waiting_date)
    dp.callback_query.register(back_to_dates, F.data == "back:dates", BookingFlow.waiting_time)
    dp.callback_query.register(choose_time, F.data.startswith("slot:"), BookingFlow.waiting_time)

    dp.message.register(got_phone, BookingFlow.waiting_phone)

    # admin actions (в личке у админов)
    dp.callback_query.register(admin_confirm, F.data.startswith("admin:confirm:"))
    dp.callback_query.register(admin_reject, F.data.startswith("admin:reject:"))

    # rules ack
    dp.callback_query.register(rules_ok, F.data.startswith("rules_ok:"))

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
