# bot.py
import os
import re
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
)
from aiogram.types import Update
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
import uvicorn

import db as booking_db
from config import SETTINGS, QUESTS, get_admin_ids
from booking_logic import generate_slots_for_date, slot_available_for_service, is_night_slot
import admin as admin_mod


# ---------- env ----------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN в Render Environment.")

WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "").strip()   # https://...onrender.com
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "").strip()   # /tg/webhook_secret

if not WEBHOOK_BASE:
    raise RuntimeError("Не найден WEBHOOK_BASE в Render Environment (например https://booking-bot-11fl.onrender.com)")
if not WEBHOOK_PATH or not WEBHOOK_PATH.startswith("/"):
    raise RuntimeError("WEBHOOK_PATH должен начинаться с '/', например /tg/webhook_kletka_2026")

WEBHOOK_URL = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH

TZ = ZoneInfo(SETTINGS.TZ)
ADMIN_IDS = set(get_admin_ids())

PHONE_RE = re.compile(r"^\+?\d[\d \-\(\)]{8,20}\d$")


def normalize_phone(s: str) -> str:
    return re.sub(r"[ \-\(\)]", "", s.strip())


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


def phone_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


class BookingFlow(StatesGroup):
    waiting_name = State()
    waiting_category = State()
    waiting_service = State()
    waiting_team = State()
    waiting_date = State()
    waiting_time = State()
    waiting_phone = State()


def is_valid_name(text: str) -> bool:
    text = text.strip()
    return 2 <= len(text) <= 60 and bool(re.fullmatch(r"[A-Za-zА-Яа-яЁё\- ]+", text))


async def start(message: Message):
    await message.answer("Привет! Я бот для бронирования квестов.", reply_markup=main_menu_kb())


async def cmd_book(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(BookingFlow.waiting_name)
    await message.answer("Как вас зовут? (только буквы/пробел/дефис)")


async def action_buttons(call: CallbackQuery, state: FSMContext):
    if call.data == "action:help":
        await call.answer()
        await call.message.edit_text(
            "• /start — меню\n• /book — бронь\n• /cancel — отмена\n• /admin — список броней (только админы)\n",
            reply_markup=main_menu_kb()
        )
        return

    if call.data == "action:book":
        await call.answer()
        await state.clear()
        await state.set_state(BookingFlow.waiting_name)
        await call.message.edit_text("Как вас зовут? (только буквы/пробел/дефис)")


async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ок, отменил.", reply_markup=main_menu_kb())


async def cmd_help(message: Message):
    await message.answer(
        "• /start — меню\n• /book — бронь\n• /cancel — отмена\n• /admin\n\n"
        "Квесты доступны: 10:00–20:30, Каннибал до 23:30.\n"
        reply_markup=main_menu_kb(),
    )


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

    if is_night_slot(service_key, slot_dt):
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

    if not slot_available_for_service(service_key, slot_iso, slot_dt):
        d = date.fromisoformat(data["date_iso"])
        await state.set_state(BookingFlow.waiting_time)
        await message.answer("Это время стало недоступно. Выберите другое:")
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
            await bot.send_message(admin_id, admin_text, reply_markup=admin_mod.admin_confirm_kb(booking_id))
        except Exception:
            pass

    await state.clear()


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(start, CommandStart())
    dp.message.register(cmd_help, Command("help"))
    dp.message.register(cmd_book, Command("book"))
    dp.message.register(cancel, Command("cancel"))

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

    # ---- admin ----
    dp.message.register(admin_mod.cmd_admin, Command("admin"))
    dp.callback_query.register(admin_mod.admin_choose_date, F.data.startswith("admin_date:"))
    dp.callback_query.register(admin_mod.admin_confirm, F.data.startswith("admin:confirm:"))
    dp.callback_query.register(admin_mod.admin_reject, F.data.startswith("admin:reject:"))
    dp.callback_query.register(admin_mod.rules_ok, F.data.startswith("rules_ok:"))

    return dp


# ---------- Webhook FastAPI ----------
app = FastAPI()

# глобальные bot/dp
bot = Bot(token=BOT_TOKEN)
dp = build_dispatcher()


@app.get("/")
def root():
    return {"status": "ok"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.model_validate(data)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid update")

    await dp.feed_update(bot, update)
    return {"ok": True}


@app.on_event("startup")
async def on_startup():
    booking_db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)


@app.on_event("shutdown")
async def on_shutdown():
    await bot.delete_webhook()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    uvicorn.run(app, host="0.0.0.0", port=port)

